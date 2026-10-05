/* Pages outside a chat: starred, reminders, my files, search, settings, admin, whoami. */
"use strict";

async function showPage(page, arg) {
  saveDraft();
  state.page = page;
  state.pageArg = arg;
  if (page !== "search") { const gs = $("#globalSearch"); if (gs) gs.value = ""; }
  closeMenus();
  renderConvList();
  sendPresence();
  setView("main");
  const main = $("#main");
  mount(main, h("div", { class: "loading" }, "Loading…"));
  const back = h("button", { class: "icon-btn back-btn", type: "button", "aria-label": "Back", onclick: backToList }, "←");
  const titles = { starred: "☆ Starred", reminders: "⏰ Reminders", calls: "📞 Calls", files: "📁 My files", search: "🔍 Search", settings: "⚙ Settings", admin: "🛡️ Admin", whoami: "👤 How the app sees you" };
  let content;
  try {
    content = await ({ starred: starredPage, reminders: remindersPage, calls: callsPage, files: myFilesPage, search: searchPage, settings: settingsPage, admin: adminPage, whoami: whoamiPage }[page])(arg);
  } catch (e) { content = h("p", { class: "error" }, e.message); }
  if (state.page !== page) return;
  mount(main, h("section", { class: "page" }, h("div", { class: "page-head" }, back, h("h2", null, titles[page] + (page === "search" ? `: “${arg}”` : ""))), h("div", { class: "page-body" }, content)));
}

async function starredPage() {
  const r = await api("api/me/starred");
  if (!r.messages.length) return h("p", { class: "hint" }, "Star a message (⋯ → ☆ Star) to keep it here. Only you see your stars.");
  const ctx = { myName: state.me.name };
  return h("div", { class: "result-list" }, r.messages.map((m) => h("div", { class: "result card-ish" },
    h("div", { class: "row" }, h("span", { class: "hint grow" }, `${m.conversationName} · ${m.userId === state.me.id ? "You" : m.author} · ${fmtFull(m.createdAt)}`),
      h("button", { class: "btn small", type: "button", onclick: () => openChatAt(m.conversationId, m.id) }, "Show in chat"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Unstar", title: "Unstar", onclick: async () => { try { await api(`api/messages/${m.id}/star`, { method: "DELETE" }); showPage("starred"); } catch (e) { fail(e); } } }, "★")),
    m.kind === "poll" ? h("div", null, "📊 " + m.poll.question) : m.kind === "card" && m.card ? cardEl(m) : m.body ? renderText(m.body, ctx) : null,
    m.attachments.length ? attachmentsEl(m) : null)));
}

async function callsPage() {
  const r = await api("api/me/calls");
  if (!r.calls.length) return h("p", { class: "hint" }, "Your calls will be listed here. Call someone with 📞 at the top of a direct chat.");
  const word = (x) => x.outcome === "answered" ? (x.outgoing ? "Outgoing" : "Incoming") + (x.seconds == null ? "" : " · " + fmtDuration(x.seconds))
    : x.missed ? "Missed" : x.outcome === "busy" ? "Busy" : x.outcome === "declined" ? (x.outgoing ? "Declined" : "You declined") : x.outcome === "missed" ? "No answer" : "Couldn't connect";
  const row = (x) => h("div", { class: "result card-ish call-row" + (x.missed ? " missed" : "") },
    avatar(x.peerName, x.peerId, { small: true, noDot: true }),
    h("div", { class: "grow" }, h("div", null, h("strong", null, x.peerName)),
      h("div", { class: "hint" }, (x.outgoing ? "↗ " : "↙ ") + word(x) + " · " + fmtFull(x.startedAt))),
    x.canCallBack ? h("button", { class: "btn small", type: "button", onclick: () => { const c = convById(x.conversationId); if (c) startCall(c); } }, "📞 Call back") : null,
    h("button", { class: "icon-btn", type: "button", title: "Open the chat", "aria-label": "Open the chat", onclick: () => x.messageId ? openChatAt(x.conversationId, x.messageId) : openChat(x.conversationId) }, "💬"));
  const missed = r.calls.filter((x) => x.missed);
  return h("div", null,
    missed.length ? h("div", { class: "lbl-sm" }, "Missed") : null, missed.length ? h("div", { class: "result-list" }, missed.map(row)) : null,
    h("div", { class: "lbl-sm" }, "Recent"), h("div", { class: "result-list" }, r.calls.map(row)));
}
async function remindersPage() {
  const r = await api("api/me/reminders");
  if (!r.reminders.length) return h("p", { class: "hint" }, "Use ⋯ → ⏰ Remind me on any message. The reminder comes to you only.");
  const open = r.reminders.filter((x) => !x.doneAt), done = r.reminders.filter((x) => x.doneAt);
  const row = (x) => h("div", { class: "result card-ish" + (x.doneAt ? " done" : "") },
    h("div", { class: "row" }, h("span", { class: "grow" }, h("strong", null, x.doneAt ? "Done " + fmtFull(x.doneAt) : "⏰ " + fmtFull(x.dueAt)),
      h("span", { class: "hint" }, ` · ${x.conversationName}${x.author ? " · " + x.author : ""}`)),
      h("button", { class: "btn small", type: "button", onclick: () => openChatAt(x.conversationId, x.messageId) }, "Show"),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Delete reminder", onclick: async () => { try { await api(`api/me/reminders/${x.id}`, { method: "DELETE" }); showPage("reminders"); } catch (e) { fail(e); } } }, "✕")),
    h("div", { class: "ellipsis" }, x.text));
  return h("div", null, open.length ? h("div", { class: "result-list" }, open.map(row)) : h("p", { class: "hint" }, "Nothing coming up."),
    done.length ? h("div", { class: "lbl-sm" }, "Done") : null, done.length ? h("div", { class: "result-list" }, done.map(row)) : null);
}

async function myFilesPage() {
  const body = h("div");
  let type = "", starred = false;
  const load = async () => { try { mount(body, filesView((await api(`api/me/files?type=${type}&starred=${starred}`)).files, true)); } catch (e) { fail(e); } };
  const tabs = h("div", { class: "tabs" }, [["", "All"], ["images", "Photos"], ["pdfs", "PDFs"], ["voice", "Voice"], ["other", "Other"]].map(([v, l]) =>
    h("button", { class: "tab" + (v === type ? " on" : ""), type: "button", onclick: (e) => { type = v; tabs.querySelectorAll(".tab").forEach((t) => t.classList.remove("on")); e.currentTarget.classList.add("on"); load(); } }, l)));
  const star = h("label", { class: "check" }, h("input", { type: "checkbox", onchange: (e) => { starred = e.target.checked; load(); } }), h("span", null, "Files in starred messages (from anyone)"));
  load();
  return h("div", null, h("p", { class: "hint" }, "Files you've shared, in chats you're still in."), tabs, star, body);
}

async function searchPage(q) {
  const r = await api(`api/search?q=${encodeURIComponent(q)}`);
  return h("div", { class: "result-list" }, searchResults(r));
}

// ---------- settings ----------
async function settingsPage() {
  const me = state.me = await api("api/me");
  const s = me.settings;
  const save = async (body) => { try { state.me = await api("api/me/settings", { method: "PUT", body }); toast("Saved"); } catch (e) { fail(e); showPage("settings"); } };
  const levels = [["all", "Every message"], ["direct_mentions", "Direct messages, mentions and replies to me"], ["off", "Nothing"]];
  const qs = h("input", { type: "time", value: s.quietStart || "", "aria-label": "Quiet from" });
  const qe = h("input", { type: "time", value: s.quietEnd || "", "aria-label": "Quiet until" });
  const themeSel = HouseholdTheme.bindSelect(h("select", { "aria-label": "Theme" }));
  const overrides = state.convs.filter((c) => c.kind !== "personal" && (c.notify !== "default" || c.muted));
  return h("div", { class: "cards" },
    h("div", { class: "card" }, h("h3", null, "Notifications"),
      me.notifyLinked ? h("p", { class: "hint" }, "📱 A phone is linked to you.") : h("div", { class: "notice" }, "No phone is linked to you yet. In Home Assistant, go to Settings → People → you → Track device and pick your phone (with the Home Assistant Companion app); it's picked up here within 5 minutes."),
      h("div", { class: "lbl-sm" }, "Tell me about"),
      levels.map(([v, l]) => h("label", { class: "check" }, h("input", { type: "radio", name: "lvl", checked: s.notifyLevel === v, onchange: () => save({ notifyLevel: v }) }), h("span", null, l))),
      field("What the notification shows", h("select", { onchange: (e) => save({ notifyPreview: e.target.value }) },
        [["full", "Sender and message"], ["sender", "Sender only"], ["none", "Nothing — just “New message”"]].map(([v, l]) => h("option", { value: v, selected: s.notifyPreview === v }, l))),
        me.app.notificationReply ? "Phone notifications have Reply and Mark as read buttons." : null),
      h("div", { class: "lbl-sm" }, "Quiet hours"),
      h("div", { class: "row wrap" }, qs, h("span", null, "to"), qe,
        h("button", { class: "btn small", type: "button", onclick: () => save({ quietStart: qs.value || null, quietEnd: qe.value || null }) }, "Save"),
        h("button", { class: "btn small ghost", type: "button", onclick: () => { qs.value = qe.value = ""; save({ quietStart: null, quietEnd: null }); } }, "Off")),
      h("p", { class: "hint" }, `Notifications wait until quiet hours end, then come as one summary. Home Assistant's time zone: ${me.timeZone}.`),
      overrides.length ? h("div", null, h("div", { class: "lbl-sm" }, "Chats with their own setting"),
        overrides.map((c) => h("div", { class: "row" }, h("button", { class: "link-btn grow", type: "button", onclick: () => { openChat(c.id).then(() => chatNotifyDialog()); } }, c.name),
          h("span", { class: "hint" }, c.muted ? "muted" : { all: "all messages", mentions: "mentions only", off: "off" }[c.notify])))) : null),
    h("div", { class: "card" }, h("h3", null, "You"),
      h("div", { class: "row" }, avatar(me.name, me.id, { big: true, noDot: true, photo: me.avatar }),
        h("div", { class: "grow" }, h("div", null, me.name),
          h("div", { class: "hint" }, "Your name and photo come from Home Assistant. To change your photo, change your picture in Home Assistant (Settings → People); it shows here within 10 minutes."))),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: s.hideOnline, onchange: (e) => save({ hideOnline: e.target.checked }) }),
        h("span", null, "Hide my online status and last seen"))),
    h("div", { class: "card" }, h("h3", null, "Look"), field("Theme", themeSel, "Shared with the other household apps.")),
    privacyCard(),
    h("p", { class: "hint" }, `Household Chat ${me.version}.`));
}

// Who can see your messages: shown to everyone in Settings (DOCS "Who can see what"; SPEC §4.1).
function privacyCard() {
  return h("div", { class: "card", id: "privacyCard" }, h("h3", null, "🔓 Who can see your messages"),
    h("p", null, "In the app, only the members of a chat can read it. Admins can't read chats they aren't in, and nobody else can open your 📌 My room."),
    h("div", { class: "notice", id: "privacyNotice" },
      h("strong", null, "Messages aren't encrypted. "),
      "They're stored readable on the Home Assistant machine, and files are ordinary files in its shared folder. Anyone who can get into that machine or its backups — including an admin who downloads the app's backup — can read every chat outside the app, My room included. Chat downloads can be read by anyone who has the file."),
    h("p", { class: "hint" }, "Fine for household chat. Don't send passwords, bank details or other secrets here — use Household Vault."));
}

// ---------- admin ----------
async function adminPage() {
  const tabs = [["people", "People"], ["chats", "Chats"], ["folders", "Shared folders"], ["settings", "App settings"], ["storage", "Storage"]];
  const body = h("div");
  const tabBar = h("div", { class: "tabs" }, tabs.map(([v, l]) => h("button", { class: "tab" + (state.adminTab === v ? " on" : ""), type: "button", onclick: () => { state.adminTab = v; showPage("admin"); } }, l)));
  const fn = { people: adminPeople, chats: adminChats, folders: adminFolders, settings: adminSettings, storage: adminStorage }[state.adminTab];
  mount(body, await fn());
  return h("div", null, tabBar, body);
}
// The shared people page (common/people.js) with this app's access (enable / disable), child accounts,
// home/away and, in a dialog, phones and extra notify services.
async function adminPeople() {
  const r = await api("api/admin/people");
  const box = h("div");
  PeoplePage.render(box, {
    people: r.people,
    intro: ["Everyone with a Home Assistant login is listed, and nobody can chat until you enable them. Enabling gives them their personal room and adds them to the Household group.",
      "📱 Phones, home/away and photos come from Home Assistant: Settings → People → (the person) — Allow person to login links them to their login, Track device picks their phone with the Companion app. Set a phone up there once and every household app uses it. People without access get no notifications."],
    checkAgain: async () => { try { await api("api/admin/people?refresh=1"); showPage("admin"); toast("Read from Home Assistant"); } catch (e) { fail(e); } },
    cardClass: "card",
    person: (x) => ({
      avatar: avatar(x.name, x.id, { small: true, noDot: true }),
      badges: [x.you ? ["you"] : null, x.disabled ? ["No access", "warn"] : ["Enabled", "accent"]],
      sub: [x.username || x.id, x.online ? "online" : x.lastSeen ? "seen " + ago(x.lastSeen) : "never seen"].join(" · "),
      controls: [
        h("label", { class: "pp-toggle", title: "Child account: can't create groups, add people, rename, pin, announce or send disappearing messages" }, "Child",
          PeoplePage.accessSwitch(x.isChild, async (on, input) => {
            try { await api(`api/admin/people/${encodeURIComponent(x.id)}`, { method: "PATCH", body: { isChild: on } }); toast(on ? `${x.name} is a child account` : `${x.name} is an adult account`); }
            catch (er) { fail(er); input.checked = !on; }
          }, { label: `${x.name} is a child account` })),
        h("button", { class: "btn small " + (x.disabled ? "primary" : "danger"), type: "button", onclick: async () => {
          if (!x.disabled && !await confirmDialog("Turn off access", `${x.name} loses access at once and leaves every group. Their messages, direct chats and personal room are kept.`, "Turn off", true)) return;
          try { await api(`api/admin/people/${encodeURIComponent(x.id)}`, { method: "PATCH", body: { disabled: !x.disabled } }); showPage("admin"); } catch (e) { fail(e); }
        } }, x.disabled ? "Enable" : "Disable"),
      ],
      blocks: h("div", { class: "pp-line" }, h("span", { class: "pp-label" }, "Phone"), h("div", { class: "pp-chips" }, PeoplePage.phoneChips(x, true))),
      actions: h("div", null, h("button", { class: "btn small" + (x.presenceMissing ? " warn" : ""), type: "button", title: x.presenceMissing ? `${x.presenceEntity} no longer exists` : "Home / away and photo", onclick: () => presenceDialog(x) },
        "🏠 ", x.presenceEntity ? x.presenceEntity.replace("person.", "") + (x.presenceMissing ? " ⚠" : "") : "–"),
        x.presenceEntity ? h("span", { class: "hint" }, x.presenceChosen ? " chosen here" : " from Home Assistant login") : null),
    }),
    notify: {
      mode: "dialog", buttonClass: "btn small", ghostClass: "btn small", openModal, api, fail,
      loadServices: () => api("api/admin/notify-services"), toast: (m, err) => toast(m, err ? { error: true } : undefined),
      path: (x) => `api/admin/people/${encodeURIComponent(x.id)}/notify`,
      testPath: (x) => `api/admin/people/${encodeURIComponent(x.id)}/notify/test`,
      texts: {
        intro: (x) => `New messages, mentions and reminders for ${x.name}${x.disabled ? " (nothing is sent while they have no access)" : ""}.`,
        phonesHelp: "Set up in Home Assistant: Settings → People → (the person) → Track device, picking their phone with the Companion app.",
      },
      onClose: () => { if (state.page === "admin") showPage("admin"); },
    },
  });
  return box;
}
async function presenceDialog(x) {
  let r = { available: false, entities: [] };
  try { r = await api("api/admin/person-entities"); } catch (e) { /* offline */ }
  const set = async (entity) => { try { await api(`api/admin/people/${encodeURIComponent(x.id)}/presence`, { method: "PUT", body: { entity } }); m.close(); showPage("admin"); } catch (e) { fail(e); } };
  const sorted = [...r.entities].sort((a, b) => (b.linkedUserId === x.id) - (a.linkedUserId === x.id) || a.name.localeCompare(b.name));
  const m = openModal(`Home / away — ${x.name}`, h("div", null,
    h("p", { class: "hint" }, "Which Home Assistant person shows whether they're home — and whose picture is their photo in the chat. Automatically it's the person linked to their login (Settings → People → Allow person to login); choose another one only if that's wrong."),
    x.presenceMissing ? h("div", { class: "notice" }, `${x.presenceEntity} no longer exists in Home Assistant.`) : null,
    r.available ? h("div", { class: "people-pick" }, sorted.map((p) => h("button", { class: "person-row" + (p.entityId === x.presenceEntity ? " on" : ""), type: "button", onclick: () => set(p.entityId) },
      h("span", { class: "grow" }, h("span", { class: "block" }, p.name + (p.linkedUserId === x.id ? " — their own Person" : "")), h("span", { class: "hint block mono" }, p.entityId)),
      h("span", { class: "chip" }, p.state || "?"))))
      : h("div", { class: "notice" }, "Home Assistant's persons couldn't be read."),
    h("div", { class: "actions" }, h("button", { class: "btn" + (x.presenceChosen ? "" : " primary"), type: "button", onclick: () => set(null) },
      "Automatic — the person linked to their login" + (x.person ? ` (${x.person})` : " (none yet)")))));
}
async function adminChats() {
  const r = await api("api/admin/conversations");
  const kindIcon = { group: "👥", direct: "💬", personal: "📌" };
  return h("div", null,
    h("div", { class: "notice info" }, "Admins can't read chats they aren't in. This page shows names, members, dates and sizes only."),
    h("table", { class: "table stack" }, h("thead", null, h("tr", null, ["Chat", "Members", "Messages", "Files", "Last activity", ""].map((t) => h("th", null, t)))),
      h("tbody", null, r.conversations.map((c) => h("tr", null,
        h("td", { "data-label": "Chat" }, kindIcon[c.kind] + " " + c.name + (c.household ? " (Household)" : "")),
        h("td", { "data-label": "Members" }, c.members ? c.members.join(", ") : "—"),
        h("td", { "data-label": "Messages" }, c.messages != null ? String(c.messages) : "—"),
        h("td", { "data-label": "Files" }, `${c.files} · ${fmtSize(c.bytes)}`),
        h("td", { "data-label": "Last activity" }, c.lastActivityAt ? fmtWhen(c.lastActivityAt) : "—"),
        h("td", { class: "cell-actions" }, c.canDelete ? h("button", { class: "btn small danger", type: "button", onclick: () => adminDeleteChat(c) }, "Delete…") : null))))));
}
function adminDeleteChat(c) {
  const input = h("input", { type: "text", "aria-label": "Group name" });
  const err = h("div", { class: "error" });
  const m = openModal("Delete group", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api(`api/admin/conversations/${c.id}`, { method: "DELETE", body: { confirmName: input.value } }); m.close(); showPage("admin"); } catch (x) { err.textContent = x.message; }
  } }, h("p", null, `Nobody with access is in ${c.name} any more. Deleting removes its messages; its files go to _deleted and are removed after 30 days.`),
  field(`Type “${c.name}” to confirm`, input), err, h("div", { class: "actions" }, h("button", { class: "btn danger", type: "submit" }, "Delete"))));
}
// ---------- the chat files folder (§5.3.1): checked before saving; changing it never moves files ----------
function storageStatus(st) {
  return st.online
    ? h("span", { class: "store-status ok" }, h("span", { class: "store-dot" }), "Connected")
    : h("span", { class: "store-status bad" }, h("span", { class: "store-dot" }), "Not connected");
}
// The folder field on App settings: what's in use now, the box, Check folder, and the check's result.
function filesFolderControl(page, folder) {
  const st = page.data.storage;
  const input = h("input", { type: "text", id: "set-files_path", value: page.value("files_path"), spellcheck: "false", autocomplete: "off",
    autocapitalize: "off", "aria-describedby": "set-files_path-help" });
  const result = h("div", { role: "status", "aria-live": "polite" });
  const draw = (c) => {
    const kind = c.refused ? "bad" : c.ok ? "good" : "warn";
    const facts = [c.exists ? (c.writable ? "exists, writable" : "exists, read-only") : "doesn't exist yet",
      c.marker === "this" ? "this install's folder" : c.marker === "other" ? "another install's folder" : null,
      c.exists ? `${c.files} file${c.files === 1 ? "" : "s"} found` : null, c.networkMount ? "network storage" : null].filter(Boolean).join(" · ");
    mount(result, h("div", { class: `sp-check ${kind}` }, h("div", null, { bad: "⛔ ", good: "✅ ", warn: "⚠️ " }[kind], c.message), h("div", { class: "sp-help" }, facts)));
  };
  folder.check = async () => {
    const p = input.value.trim();
    mount(result, h("div", { class: "hint" }, "Checking the folder…"));
    try {
      const c = await api("api/admin/settings/check-files-path", { method: "POST", body: { path: p } });
      if (input.value.trim() !== p) return null;
      folder.last = { path: p, c }; draw(c); return c;
    } catch (e) { folder.last = null; mount(result, h("div", { class: "error" }, e.message)); return null; }
  };
  input.addEventListener("input", () => { folder.last = null; clear(result); page.set("files_path", input.value.trim()); });
  return h("div", { class: "sp-folder" },
    h("div", { class: "kv" },
      h("span", { class: "k" }, "In use now"), h("span", null, h("code", { class: "path" }, st.path)),
      h("span", { class: "k" }, "Status"), h("span", null, storageStatus(st), st.networkMount ? h("span", { class: "hint" }, " · network storage") : null)),
    !st.online ? h("div", { class: "notice danger" }, st.reason) : null,
    h("div", { class: "sp-folder-row" }, input, h("button", { class: "btn", type: "button", onclick: () => folder.check() }, "Check folder")),
    result);
}
// Admin → App settings: drawn by common/settings.js from the server's description of each setting.
async function adminSettings() {
  const box = h("div");
  const folder = { last: null, check: null };
  await SettingsPage.render(box, {
    load: () => api("api/admin/settings"),
    save: (body) => api("api/admin/settings", { method: "PUT", body }),
    classes: { card: "card", primary: "btn primary", secondary: "btn", ghost: "btn" },
    fields: { files_path: { control: (page) => filesFolderControl(page, folder) } },
    groups: { calls: { bottom: () => testCallingBlock() } },
    // changing the chat files folder: checked first, refused or confirmed as the check says; never moves files
    beforeSave: async (body, page) => {
      if ("files_path" in body) {
        const p = body.files_path;
        const c = folder.last && folder.last.path === p ? folder.last.c : await folder.check();
        if (!c) return null;
        if (c.verdict === "current") delete body.files_path;
        else {
          if (c.refused) { page.setError("files_path", c.message); return null; }
          if (c.needsConfirm) {
            if (!await confirmDialog("Change the chat files folder", c.message + " Change the folder anyway?", "Change folder", true)) return null;
            body.confirm = true;
          }
        }
      }
      return body;
    },
    afterSave: async (out, body) => {
      state.me = await api("api/me").catch(() => state.me);
      if ("files_path" in body) {
        renderFilesBanner();
        if (out.storage.online) { toast("Saved — the chat's files now go to " + out.storage.path); return "Saved — the chat's files now go to " + out.storage.path; }
        toast("Saved, but the folder isn't connected: " + out.storage.reason, { error: true });
        return "Saved, but the folder isn't connected: " + out.storage.reason;
      }
      toast("Saved — applies straight away");
      return "Saved — applies straight away";
    },
  });
  // Connected apps (APP_MESSAGES_SPEC §5): read only, drawn by common/connected-apps.js
  const apps = h("div", { class: "connected-apps-wrap" });
  api("api/admin/connected-apps").then((d) => mount(apps, ConnectedApps.card(d, { h, when: fmtFull }))).catch(() => {});
  return h("div", null, box, apps);
}
// Test calling (§15.13): the browser checks the microphone and that it reaches the STUN server and the relay
// as a call would — the addresses come from the saved settings, so Save first.
function testCallingBlock() {
  const out = h("div", { class: "hint test-call-out", "aria-live": "polite" });
  const btn = h("button", { class: "btn", type: "button", onclick: () => runCallTest(out, btn) }, "Test calling");
  return h("div", { class: "test-call" }, btn, h("span", { class: "hint" }, " Checks the microphone, the address lookup and the relay with the saved settings."), out);
}
async function runCallTest(out, btn) {
  btn.disabled = true;
  const lines = [];
  const say = (t) => { lines.push(t); mount(out, lines.map((l) => h("div", null, l))); };
  try {
    if (!micPossible()) say("✗ Microphone: this browser can't use it here (https and permission are needed).");
    else { try { const s = await getMic(); s.getTracks().forEach((t) => t.stop()); say("✓ Microphone works."); } catch (e) { say("✗ Microphone permission was refused."); } }
    const t = await api("api/admin/calls/ice-servers");
    if (!t.stun && t.relay === "none") say("ℹ No address lookup or relay is set: calls work on the home network only.");
    if (t.relay !== "none" && !t.relayOk) say("✗ Relay: no credentials — " + (t.relayError ? t.relayError + ". " : "") + (t.relay === "cloudflare" ? "The key id is the TURN key's Token ID from Realtime → TURN (not a Realtime app id), and the API token is the one shown when that TURN key was made." : "Check the address and secret, and the app's Log tab."));
    if (!window.RTCPeerConnection) { say("✗ This browser can't make calls."); return; }
    const found = { host: 0, srflx: 0, relay: 0 };
    const pc = new RTCPeerConnection({ iceServers: t.iceServers });
    pc.createDataChannel("test");
    pc.onicecandidate = (e) => { if (e.candidate && e.candidate.type in found) found[e.candidate.type] += 1; };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((r) => { pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === "complete") r(); }; setTimeout(r, 8000); });
    pc.close();
    say(found.host ? "✓ Home network: this device is reachable directly." : "✗ No local network address found.");
    if (t.stun) say(found.srflx ? "✓ Address lookup (STUN): this device's public address was found." : "✗ Address lookup (STUN): no answer from the server — check the address and that this network allows it.");
    if (t.relay !== "none" && t.relayOk) say(found.relay ? "✓ Relay: a relayed connection is possible from here." : "✗ Relay: couldn't get a relayed address — check the relay's address, port forwarding (own server) or the credentials.");
    if (!lines.some((l) => l.startsWith("✗"))) say("All good.");
  } catch (e) { say("✗ " + e.message); } finally { btn.disabled = false; }
}
async function adminStorage() {
  const s = await api("api/admin/storage");
  const max = Math.max(1, ...s.chats.map((c) => c.bytes));
  const inclFiles = h("input", { type: "checkbox" });
  const restoreFile = h("input", { type: "file", accept: ".zip", "aria-label": "Backup file" });
  const quotaPct = s.quotaGb ? Math.round(s.filesBytes / (s.quotaGb * 1024 ** 3) * 100) : null;
  const st = s.storage;
  const checkAgain = async (useThisFolder) => {
    if (useThisFolder && !await confirmDialog("Use this folder", `Set up ${st.path} as this install's chat files folder? Only do this if these are this chat's files (for example after moving them without the .household_chat_store file). Nothing is moved or deleted.`, "Use this folder", true)) return;
    try {
      const r = await api("api/admin/files-storage/check", { method: "POST", body: useThisFolder ? { useThisFolder: true } : {} });
      toast(r.online ? "Connected" : "Still not connected: " + r.reason, r.online ? {} : { error: true });
      state.me = await api("api/me").catch(() => state.me); renderFilesBanner(); showPage("admin");
    } catch (e) { fail(e); }
  };
  return h("div", { class: "cards" },
    h("div", { class: "card" }, h("h3", null, "File storage"),
      h("div", { class: "kv" },
        h("span", { class: "k" }, "Folder"), h("span", null, h("code", { class: "path" }, st.path)),
        h("span", { class: "k" }, "Status"), h("span", null, storageStatus(st)),
        h("span", { class: "k" }, "Checked"), h("span", null, st.checkedAt ? new Date(st.checkedAt).toLocaleString() : "not yet"),
        st.networkMount ? h("span", { class: "k" }, "Kind") : null, st.networkMount ? h("span", null, "Network storage") : null,
        st.freeBytes !== null ? h("span", { class: "k" }, "Free space") : null, st.freeBytes !== null ? h("span", null, fmtSize(st.freeBytes)) : null,
        s.waitingJobs ? h("span", { class: "k" }, "Waiting deletes") : null, s.waitingJobs ? h("span", null, `${s.waitingJobs} (done when it's back)`) : null),
      !st.online ? h("div", { class: "notice danger" }, st.reason, " While it isn't connected, chats work but files can't be sent or opened, and deleted files are removed once it's back.") : null,
      h("div", { class: "row wrap" },
        h("button", { class: "btn", type: "button", onclick: () => checkAgain(false) }, "Check again"),
        !st.online ? h("button", { class: "btn danger", type: "button", onclick: () => checkAgain(true) }, "Use this folder") : null,
        h("button", { class: "btn", type: "button", onclick: () => { state.adminTab = "settings"; showPage("admin"); } }, "Change folder…"))),
    h("div", { class: "card" }, h("h3", null, "Space used"),
      h("div", { class: "kv" }, h("span", { class: "k" }, "Chat files"), h("span", null, fmtSize(s.filesBytes) + (s.quotaGb ? ` of ${s.quotaGb} GB (${quotaPct}%)` : "")),
        h("span", { class: "k" }, "Database"), h("span", null, fmtSize(s.dbBytes)),
        h("span", { class: "k" }, "Files no longer available"), h("span", null, String(s.missing)),
        h("span", { class: "k" }, "Uploads not sent yet"), h("span", null, String(s.pending))),
      quotaPct !== null && quotaPct >= 80 ? h("div", { class: "notice" }, "The chat folder is over 80% of its limit.") : null,
      h("div", { class: "bars" }, s.chats.filter((c) => c.bytes).map((c) => {
        const bar = h("span", { class: "bar-fill" }); bar.style.width = Math.max(2, Math.round(c.bytes / max * 100)) + "%";
        return h("div", { class: "bar-row" }, h("span", { class: "ellipsis bar-name" }, c.name), h("span", { class: "bar" }, bar), h("span", { class: "hint" }, fmtSize(c.bytes)));
      })),
      h("button", { class: "btn", type: "button", onclick: async () => { try { const r = await api("api/admin/storage/check", { method: "POST" }); toast(`${r.missing} missing · ${r.thumbsRemoved} old previews removed`); showPage("admin"); } catch (e) { fail(e); } } }, "Check files")),
    await storageCleanupCard(),
    retentionCard(s.retention),
    h("div", { class: "card" }, h("h3", null, "Backup and restore"),
      h("div", { class: "notice" }, "A backup contains every message readable, and with files every shared file. Anyone who has it can read all chats — keep it safe."),
      h("label", { class: "check" }, inclFiles, h("span", null, "Include files (can be large)")),
      h("button", { class: "btn", type: "button", onclick: async () => { try { toast("Preparing the backup…"); const res = await api(`api/admin-storage-download-db?includeFiles=${inclFiles.checked}`, { raw: true }); const cd = res.headers.get("content-disposition") || ""; const m = /filename="?([^";]+)"?/.exec(cd); saveBlob(await res.blob(), m ? m[1] : "household-chat-backup.zip"); } catch (e) { fail(e); } } }, "⬇ Download backup"),
      h("div", { class: "row wrap" }, restoreFile, h("button", { class: "btn danger", type: "button", onclick: async () => {
        const f = restoreFile.files[0];
        if (!f) { toast("Choose a backup .zip first.", { error: true }); return; }
        if (!await confirmDialog("Restore backup", "Replace every message with this backup? Messages sent after it was taken are lost. Files in the backup are put back.", "Restore", true)) return;
        try { const r = await api("api/admin-storage-import-db", { method: "POST", rawBody: f }); toast(`Restored${r.files ? ` with ${r.files} files` : ""} — reloading`); setTimeout(() => location.reload(), 1200); } catch (e) { fail(e); }
      } }, "Restore…"))));
}

// ---------- storage clean-up (§15.10) ----------
function retentionCard(r) {
  if (!r) return null;
  return h("div", { class: "card" }, h("h3", null, "Old messages"),
    h("div", { class: "kv" },
      h("span", { class: "k" }, "Older than 30 days"), h("span", null, String(r.olderThan["30"])),
      h("span", { class: "k" }, "Older than 90 days"), h("span", null, String(r.olderThan["90"])),
      h("span", { class: "k" }, "Older than a year"), h("span", null, String(r.olderThan["365"]))),
    h("p", { class: "hint" }, r.settingDays ? `App settings delete messages after ${r.settingDays} days; tonight that removes ${r.wouldRemove} (pinned ones are kept).`
      : "Old messages are kept forever. App settings can delete them after a number of days."));
}
async function storageCleanupCard() {
  const usage = await api("api/admin/storage/usage");
  const box = h("div");
  let type = "", older = 0;
  const picks = new Set();
  const typeLbl = { photos: "Photos", videos: "Videos", audio: "Audio", documents: "Documents", other: "Other" };
  const load = async () => {
    picks.clear();
    let r;
    try { r = await api(`api/admin/storage/files?type=${type}&olderThanDays=${older}&limit=100`); } catch (e) { fail(e); return; }
    const del = h("button", { class: "btn small danger", type: "button", disabled: true, onclick: async () => {
      if (!await confirmDialog("Delete files", `Delete ${picks.size} file(s)? Their messages will say "File deleted by admin". They go to _deleted for 30 days (disappearing ones are removed at once).`, "Delete", true)) return;
      try { const x = await api("api/admin/storage/delete", { method: "POST", body: { attachmentIds: [...picks] } }); toast(`${x.deleted} deleted`); showPage("admin"); } catch (e) { fail(e); }
    } }, "Delete selected");
    mount(box, r.files.length ? h("table", { class: "table stack" }, h("thead", null, h("tr", null, ["", "File", "Chat", "From", "Date", "Size"].map((t) => h("th", null, t)))),
      h("tbody", null, r.files.map((f) => h("tr", null,
        h("td", { class: "cell-actions" }, h("input", { type: "checkbox", "aria-label": "Select", onchange: (e) => { if (e.target.checked) picks.add(f.id); else picks.delete(f.id); del.disabled = !picks.size; } })),
        h("td", { "data-label": "File" }, f.name ? f.name : h("span", { class: "hint" }, f.type + (f.disappearing ? " (disappearing)" : "") + " — name hidden")),
        h("td", { "data-label": "Chat" }, f.chat),
        h("td", { "data-label": "From" }, f.sender),
        h("td", { "data-label": "Date" }, fmtWhen(f.sentAt)),
        h("td", { "data-label": "Size" }, fmtSize(f.size)))))) : h("p", { class: "hint" }, "No files."), r.files.length ? h("div", { class: "actions" }, del) : null);
  };
  const typeSel = h("select", { "aria-label": "Type", onchange: (e) => { type = e.target.value; load(); } },
    h("option", { value: "" }, "All types"), Object.entries(typeLbl).map(([v, l]) => h("option", { value: v }, l)));
  const ageSel = h("select", { "aria-label": "Age", onchange: (e) => { older = Number(e.target.value); load(); } },
    h("option", { value: "0" }, "Any age"), h("option", { value: "182" }, "Older than 6 months"), h("option", { value: "365" }, "Older than a year"));
  load();
  return h("div", { class: "card" }, h("h3", null, "Clean up"),
    h("div", { class: "row wrap" }, Object.entries(typeLbl).map(([k, l]) => h("span", { class: "chip" }, `${l}: ${fmtSize(usage.byType[k])}`))),
    h("p", { class: "hint" }, "The largest files. For chats you aren't in, only the type and size are shown."),
    h("div", { class: "row wrap" }, typeSel, ageSel), box,
    h("div", { class: "row wrap" },
      h("button", { class: "btn small", type: "button", onclick: async () => { if (!await confirmDialog("Empty _deleted", "Remove everything in _deleted now instead of after 30 days?", "Empty now", true)) return; try { await api("api/admin/storage/empty-deleted", { method: "POST" }); toast("Emptied"); showPage("admin"); } catch (e) { fail(e); } } }, "Empty _deleted now"),
      h("button", { class: "btn small", type: "button", onclick: async () => { try { await api("api/admin/storage/thumbs-delete", { method: "POST" }); toast("Previews deleted — they're made again when needed"); } catch (e) { fail(e); } } }, "Delete previews")));
}

// ---------- shared folders (admin) ----------
async function adminFolders() {
  const r = await api("api/admin/folders");
  const modeName = { ro: "Read-only", rw: "Read and write" };
  const addBtn = h("button", { class: "btn primary", type: "button", disabled: !r.chats.length, onclick: () => folderDialog(r.chats) }, "＋ Share a folder");
  return h("div", null,
    h("p", { class: "hint" }, "Share an existing folder from Home Assistant's /share (for example Documents/House) into any chats — groups, direct chats or personal rooms, several at once — each read-only or read and write. Members browse it from the chat's 📂 button, and new files are announced in the chat (not in personal rooms). Admins don't need to be in the chat."),
    addBtn,
    r.folders.length ? h("div", { class: "shared-list" }, r.folders.map((f) => h("div", { class: "card shared-card" },
      h("div", { class: "shared-head" },
        h("div", { class: "grow" }, h("div", { class: "shared-title" }, "📂 " + f.label),
          h("div", { class: "hint mono path-wrap" }, "/share/" + f.path), !f.exists ? h("div", { class: "error" }, "This folder is missing in /share.") : null),
        h("div", { class: "row wrap shared-actions" },
          h("button", { class: "btn small", type: "button", onclick: () => folderDialog(r.chats, f) }, "Edit…"),
          h("button", { class: "btn small danger", type: "button", onclick: async () => {
            if (!await confirmDialog("Stop sharing", `Stop sharing “${f.label}” in ${f.chats.length === 1 ? "its chat" : `all ${f.chats.length} chats`}? The folder and its files stay where they are.`, "Stop sharing", true)) return;
            try { await api(`api/admin/folders/${f.id}`, { method: "DELETE" }); showPage("admin"); } catch (x) { fail(x); }
          } }, "Stop sharing"))),
      h("div", { class: "shared-chats" }, f.chats.map((c) => h("span", { class: "chip" + (c.mode === "rw" ? " on" : "") }, `${c.name} · ${modeName[c.mode]}`))),
      h("div", { class: "hint" }, f.announce ? "New files are announced in the chats." : "New files aren't announced.")))) : h("p", { class: "hint" }, "No folders shared yet."));
}
// one dialog to share a new folder, or change an existing one's name, chats and access
function folderDialog(chatsList, existing) {
  let path = existing ? existing.path : "";
  const picked = new Map((existing ? existing.chats : []).map((c) => [c.id, c.mode]));
  const label = h("input", { type: "text", maxlength: "60", "aria-label": "Name in the chats", value: existing ? existing.label : "" });
  const announce = h("input", { type: "checkbox", checked: existing ? existing.announce : true });
  const err = h("div", { class: "error" });
  let pickerPart = null;
  if (!existing) {
    const browser = h("div", { class: "file-rows folder-picker" });
    const cur = h("div", { class: "mono path-wrap" });
    const load = async () => {
      let r;
      try { r = await api(`api/admin/share-dirs?path=${encodeURIComponent(path)}`); } catch (e) { fail(e); return; }
      path = r.path;
      cur.textContent = "/share/" + r.path;
      if (!label.value || label.dataset.auto) { label.value = r.path.split("/").pop() || ""; label.dataset.auto = "1"; }
      mount(browser,
        r.path ? h("button", { class: "file-chip folder-row", type: "button", onclick: () => { path = r.path.split("/").slice(0, -1).join("/"); load(); } }, "⬆ Up") : null,
        r.folders.length ? r.folders.map((f) => h("button", { class: "file-chip folder-row", type: "button", onclick: () => { path = (r.path ? r.path + "/" : "") + f; load(); } }, h("span", { class: "file-icon" }, "📁"), f))
          : h("p", { class: "hint" }, "No folders inside."));
    };
    label.addEventListener("input", () => { delete label.dataset.auto; });
    pickerPart = h("div", null, h("div", { class: "lbl-sm" }, "Folder (open it, then share)"), cur, browser);
    load();
  }
  // the chats: tick any number; each has its own access
  const rows = chatsList.map((c) => {
    const box = h("input", { type: "checkbox", checked: picked.has(c.id), "aria-label": c.name });
    const mode = h("select", { "aria-label": "Access in " + c.name, disabled: !picked.has(c.id) },
      h("option", { value: "ro" }, "Read-only"), h("option", { value: "rw" }, "Read and write"));
    mode.value = picked.get(c.id) || "ro";
    box.addEventListener("change", () => { if (box.checked) picked.set(c.id, mode.value); else picked.delete(c.id); mode.disabled = !box.checked; count(); });
    mode.addEventListener("change", () => { if (box.checked) picked.set(c.id, mode.value); });
    const row = h("div", { class: "pick-row" }, h("label", { class: "check grow" }, box, h("span", { class: "ellipsis" }, c.name)), mode);
    row.dataset.name = c.name.toLowerCase();
    return row;
  });
  const counter = h("span", { class: "hint" });
  const count = () => { counter.textContent = picked.size ? `${picked.size} chat${picked.size === 1 ? "" : "s"} chosen` : "No chats chosen"; };
  count();
  const filter = chatsList.length > 8 ? h("input", { type: "search", placeholder: "Find a chat", "aria-label": "Find a chat",
    oninput: (e) => { const q = e.target.value.trim().toLowerCase(); rows.forEach((r) => { r.hidden = !!q && !r.dataset.name.includes(q); }); } }) : null;
  const m = openModal(existing ? "Edit shared folder" : "Share a folder", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    if (!existing && !path) { err.textContent = "Open the folder to share first."; return; }
    if (!picked.size) { err.textContent = existing ? "Choose at least one chat — or use Stop sharing." : "Choose at least one chat."; return; }
    const body = { label: label.value, announce: announce.checked, chats: [...picked].map(([id, mode]) => ({ id, mode })) };
    try {
      if (existing) await api(`api/admin/folders/${existing.id}`, { method: "PATCH", body });
      else await api("api/admin/folders", { method: "POST", body: Object.assign({ path }, body) });
      m.close(); toast(existing ? "Saved" : "Shared"); showPage("admin");
    } catch (x) { err.textContent = x.message; }
  } }, existing ? h("div", { class: "mono hint path-wrap" }, "/share/" + existing.path) : pickerPart,
  field("Name in the chats", label),
  h("label", { class: "check" }, announce, h("span", null, "Announce new files in the chats")),
  h("div", { class: "row wrap pick-head" }, h("div", { class: "lbl-sm grow" }, "Share with"), counter),
  filter, h("div", { class: "pick-list" }, rows), err,
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, existing ? "Save" : "Share this folder"))), { wide: true, noFocus: true });
}

// ---------- whoami ----------
// Drawn by common/whoami.js: the same rows and wording as every other app.
async function whoamiPage() {
  const w = await api("api/whoami");
  return h("div", { class: "card" }, HouseholdWhoami.panel(w, {
    appName: "Household Chat",
    classes: { row: null, label: "k", value: null },
    copyGlyph: "📋",
    onCopy: (text) => copyText(text),
  }));
}

// ---------- first run: nobody is an admin yet (SPEC §4.2) ----------
// Shown to everyone on every page (it sits above #app), until admin_users has at least one name.
function renderSetupBanner() {
  const el = $("#setupBanner");
  if (!el) return;
  if (!state.me || !state.me.noAdmin) { clear(el); el.hidden = true; return; }
  const openWhoami = async () => {
    if (state.me.disabled) { try { openModal("How the app sees you", await whoamiPage()); } catch (e) { fail(e); } }
    else showPage("whoami");
  };
  el.hidden = false;
  mount(el, h("div", { class: "setup-banner", role: "alert" },
    HouseholdWhoami.noAdminBanner(state.me.username || state.me.id, { onOpen: openWhoami })));
}

// ---------- no access yet ----------
function renderDisabled() {
  mount($("#app"), h("div", { class: "center" }, h("div", { class: "card narrow" },
    h("div", { class: "brand-big" }, "💬"), h("h1", null, "Household Chat"),
    h("p", null, "Ask an admin to give you access."),
    h("p", { class: "hint" }, `You're signed in to Home Assistant as ${state.me.name}. An admin enables people in Admin → People.`),
    state.me.isAdmin ? h("div", { class: "notice info" }, "You're an admin: enable yourself first.", h("div", { class: "actions" },
      h("button", { class: "btn primary", type: "button", onclick: async () => { try { await api(`api/admin/people/${encodeURIComponent(state.me.id)}`, { method: "PATCH", body: { disabled: false } }); location.reload(); } catch (e) { fail(e); } } }, "Enable me"),
      h("button", { class: "btn", type: "button", onclick: () => adminOnlyShell() }, "Open Admin"))) : null,
    h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: async () => { const c = await whoamiPage(); openModal("How the app sees you", c); } }, "How the app sees you")))));
}
async function adminOnlyShell() {
  mount($("#app"), h("div", { class: "admin-only" }, h("main", { id: "main", class: "main" })));
  state.page = "admin";
  const main = $("#main");
  mount(main, h("section", { class: "page" }, h("div", { class: "page-head" }, h("button", { class: "icon-btn", type: "button", onclick: () => location.reload() }, "←"), h("h2", null, "🛡️ Admin")),
    h("div", { class: "page-body" }, await adminPage())));
  state.adminOnly = true;
}
