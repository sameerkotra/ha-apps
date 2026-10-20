/* The chat list, the open chat, messages and the composer. */
"use strict";

// ---------- shell ----------
function renderShell() {
  const app = $("#app");
  mount(app,
    h("div", { class: "shell", id: "shell", "data-view": state.current || state.page !== "chat" ? "main" : "list" },
      h("aside", { class: "side", id: "side" },
        h("div", { class: "side-top" },
          h("span", { class: "brand" }, "💬 Household Chat"),
          h("span", { class: "spacer" }),
          h("button", { class: "icon-btn", type: "button", title: "New chat", "aria-label": "New chat", onclick: (e) => newChatMenu(e.currentTarget) }, "＋"),
          h("button", { class: "icon-btn", type: "button", title: "More", "aria-label": "More", onclick: (e) => mainMenu(e.currentTarget) }, "⋯")),
        h("div", { class: "side-search" },
          h("input", { type: "search", id: "globalSearch", placeholder: "Search messages and files", "aria-label": "Search messages and files",
            onkeydown: (e) => { if (e.key === "Enter" && e.target.value.trim()) showPage("search", e.target.value.trim(), { tab: true }); } }),
          h("button", { class: "chip filter-chip", id: "unreadChip", type: "button", hidden: true, "aria-pressed": "false", title: "Show only chats with unread messages",
            onclick: () => { state.unreadOnly = !state.unreadOnly; renderConvList(); } }, "Unread")),
        h("div", { id: "filesBanner" }),
        h("div", { class: "conv-list", id: "convList", role: "list" }),
        h("div", { class: "side-foot" },
          h("button", { class: "me-btn grow", type: "button", title: "Me", "aria-label": "Me: settings, theme, how the app sees you", "aria-haspopup": "menu", onclick: (e) => meMenu(e.currentTarget) },
            avatar(state.me.name, state.me.id, { small: true, noDot: true }),
            h("span", { class: "grow ellipsis" }, state.me.name),
            h("span", { class: "me-caret", "aria-hidden": "true" }, "▾")),
          h("span", { class: "conn-dot", id: "connDot", title: "Live updates" }))),
      h("main", { class: "main", id: "main" })));
  renderConvList();
  renderFilesBanner();
}
// the chat files folder isn't connected (§5.3.1): chats work, files wait
function renderFilesBanner() {
  const el = $("#filesBanner");
  if (!el) return;
  const f = state.me && state.me.files;
  if (!f || f.online) { clear(el); return; }
  mount(el, h("div", { class: "notice danger files-banner", role: "status" }, "📎 File storage isn't connected. Messages work, but files can't be sent or opened until it's back. ",
    state.me.isAdmin ? h("button", { class: "link-btn", type: "button", onclick: () => { state.adminTab = "storage"; showPage("admin"); } }, "See Storage") : null));
}
// On a phone the list and the open chat (or page) are one pane at a time (data-view); Back from a chat or a page
// returns to the list (core.js: navigate / BackNav).
function setView(v) {
  const s = $("#shell");
  if (s) s.setAttribute("data-view", v);
}
// the start page: the chat list (on a computer with the welcome text beside it), no address
function showHome() {
  saveDraft();
  state.current = null; state.page = "chat";
  if (location.hash) { try { history.replaceState(history.state, "", location.pathname + location.search); } catch (e) { /* sandboxed */ } }
  renderConvList();
  renderEmptyMain();
  sendPresence();
  if (routing) BackNav.sync();
}
// ＋: new conversations and calls
function newChatMenu(anchor) {
  menu(anchor, [
    { label: "💬 Direct message", run: () => newDirectDialog() },
    !isChild() && (state.me.app.whoCanCreateGroups === "everyone" || state.me.isAdmin) ? { label: "👥 New group", run: () => newGroupDialog() } : null,
    callsOn() ? { label: "📞 New call…", run: () => newCallDialog() } : null,
  ]);
}
// ⋯: the content pages
function mainMenu(anchor) {
  menu(anchor, [
    { label: "📌 My room", run: () => openChat(state.me.personalRoomId, { tab: true }) },
    { label: "☆ Starred", run: () => showPage("starred", null, { tab: true }) },
    { label: "⏰ Reminders", run: () => showPage("reminders", null, { tab: true }) },
    callsOn() ? { label: "📞 Calls", run: () => showPage("calls", null, { tab: true }) } : null,
    { label: "📁 My files", run: () => showPage("files", null, { tab: true }) },
    { label: "🕓 Scheduled", run: () => scheduledDialog(null) },
  ]);
}
// Me (your name at the foot of the list): settings, theme, the diagnostic page and Admin
function meMenu(anchor) {
  menu(anchor, [
    { label: "⚙ Settings", run: () => showPage("settings", null, { tab: true }) },
    { label: "🎨 Theme…", run: () => themeDialog() },
    { label: "👤 How the app sees you", run: () => showPage("whoami", null, { tab: true }) },
    state.me.isAdmin ? { label: "🛡️ Admin", run: () => showPage("admin", null, { tab: true }) } : null,
  ]);
}
function themeDialog() {
  openModal("Theme", field("Theme", HouseholdTheme.bindSelect(h("select", { "aria-label": "Theme" })), "Shared with the other household apps."));
}

// ---------- chat list ----------
function renderConvList() {
  const box = $("#convList");
  if (!box) return;
  const many = state.convs.length > 8;
  const chip = $("#unreadChip");
  if (chip) { chip.hidden = !many; chip.classList.toggle("on", many && state.unreadOnly); chip.setAttribute("aria-pressed", String(many && state.unreadOnly)); }
  const only = many && state.unreadOnly;
  const shown = only ? state.convs.filter((c) => c.unread || c.mentionUnread || c.id === state.current) : state.convs;
  const rows = [];
  let pinnedHead = false, afterPinned = false;
  shown.forEach((c) => {
    const pinned = c.pinned && c.kind !== "personal";
    if (pinned && !pinnedHead) { pinnedHead = true; rows.push(h("div", { class: "conv-divider", "aria-hidden": "true" }, "Pinned")); }
    else if (!pinned && pinnedHead && !afterPinned && c.kind !== "personal") { afterPinned = true; rows.push(h("div", { class: "conv-divider line", "aria-hidden": "true" })); }
    rows.push(convRow(c));
  });
  if (only && !shown.length) rows.push(h("p", { class: "hint conv-none" }, "No unread chats. ", h("button", { class: "link-btn", type: "button", onclick: () => { state.unreadOnly = false; renderConvList(); } }, "Show all")));
  mount(box, rows);
  updateTitle();
}
function convRow(c) {
  const last = c.lastMessage;
  const typing = typingText(c.id);
  const draft = !typing && c.draft && c.draft.body && c.id !== state.current ? c.draft.body : null;
  let preview = typing || (last ? (last.by && last.by !== state.me.id && c.kind === "group" ? `${last.author}: ` : last.by === state.me.id && c.kind !== "personal" ? "You: " : "") + last.preview : (c.kind === "personal" ? "Only you can see this in the app" : ""));
  const sub = c.kind === "direct" && c.otherUserId ? (state.homeAway[c.otherUserId] || {}).label : null;
  const active = c.id === state.current && state.page === "chat";
  return h("button", {
    class: "conv" + (active ? " active" : "") + (c.unread ? " unread" : ""), type: "button", role: "listitem", "aria-current": active ? "true" : null,
    onclick: () => openChat(c.id, { tab: true }), "aria-label": `${c.name}${c.unread ? `, ${c.unread} unread` : ""}`,
  },
    convIcon(c),
    h("span", { class: "conv-main" },
      h("span", { class: "conv-top" },
        h("span", { class: "conv-name ellipsis" }, c.name),
        c.muted ? h("span", { class: "hint", title: "Muted" }, "🔕") : null,
        c.disappearSeconds ? h("span", { class: "hint", title: "Disappearing messages: " + durationLabel(c.disappearSeconds) }, "⏱") : null,
        h("span", { class: "conv-time" }, last ? fmtWhen(last.at) : "")),
      sub ? h("span", { class: "conv-presence ellipsis" }, sub) : null,
      h("span", { class: "conv-bottom" },
        draft ? h("span", { class: "conv-preview ellipsis" }, h("span", { class: "draft-lbl" }, "Draft: "), plainText(draft))
          : h("span", { class: "conv-preview ellipsis" + (typing ? " typing" : "") }, preview),
        c.mentionUnread ? h("span", { class: "badge at", title: "Mentions you" }, "@") : null,
        c.unread ? h("span", { class: "badge" + (c.muted ? " muted" : "") }, c.unread > 99 ? "99+" : String(c.unread)) : null)));
}
function updateTitle() {
  const n = state.convs.filter((c) => !c.muted).reduce((a, c) => a + (c.unread ? 1 : 0), 0);
  document.title = (n ? `(${n}) ` : "") + "Household Chat";
}
async function loadConvs() {
  const r = await api("api/conversations");
  state.convs = r.conversations;
  state.online = r.online || {};
  state.homeAway = r.homeAway || {};
  renderConvList();
  if (state.current && !convById(state.current)) {
    if (state.page === "chat") showHome(); else state.current = null;
  }
}
async function loadPeople() {
  try { state.people = (await api("api/people")).people; } catch (e) { /* ignore */ }
}

// ---------- the open chat ----------
function renderEmptyMain() {
  mount($("#main"), h("div", { class: "empty-main" },
    h("div", { class: "brand-big" }, "💬"),
    h("p", null, "Choose a chat, or start one with ＋."),
    h("p", { class: "hint" }, "📌 My room is a space only you can see in the app — for notes, links and documents."),
    h("p", { class: "hint" }, "Messages aren't encrypted: whoever can get into the Home Assistant machine or its backups can read them. ",
      h("button", { class: "link-btn", type: "button", onclick: () => showPage("settings") }, "Who can see your messages"))));
  setView("list");
}
// Open a chat: its address ("#/c/<id>", "/m/<message>" to open it at a message); the route draws it (loadChat).
// opts.tab: from the chat list or a menu (Back then goes to the list); opts.replace: without remembering where you were.
function openChat(cid, opts = {}) {
  if (!cid) return Promise.resolve();
  const hash = "#/c/" + encodeURIComponent(cid) + (opts.around ? "/m/" + encodeURIComponent(opts.around) : "");
  const same = state.page === "chat" && state.current === cid;
  return navigate(hash, { tab: opts.tab, replace: opts.replace || same });
}
async function loadChat(cid, opts = {}) {
  if (!cid) return;
  saveDraft();
  state.page = "chat";
  state.current = cid;
  state.editing = null;
  state.selecting = null;
  state.newBelow = 0;
  setView("main");
  renderConvList();
  UI.loading($("#main"));
  try {
    const [detail, page] = await Promise.all([api(`api/conversations/${cid}`), api(`api/conversations/${cid}/messages` + (opts.around ? `?around=${opts.around}` : ""))]);
    if (state.current !== cid) return;
    state.detail = detail;
    state.msgs = page.messages;
    state.moreBefore = page.moreBefore;
    state.moreAfter = page.moreAfter;
    state.reads = page.reads;
    const conv = convById(cid) || detail;
    const firstUnread = state.msgs.find((m) => m.id > (conv.lastReadId || 0) && m.userId && m.userId !== state.me.id);
    state.unreadMarker = firstUnread && conv.unread ? firstUnread.id : null;
    renderChat();
    if (opts.around) scrollToMessage(opts.around, true);
    else if (state.unreadMarker) scrollToMessage(state.unreadMarker, false, true);
    else scrollBottom();
    sendPresence();
    markReadSoon();
  } catch (e) {
    if (state.current !== cid) return;
    if (e.status === 404) { fail(e); state.current = null; await loadConvs().catch(() => {}); showHome(); return; }
    if (e.status === -1) return;
    mount($("#main"), h("section", { class: "page" },
      h("div", { class: "page-head" }, h("button", { class: "icon-btn back-btn", type: "button", "aria-label": "Back", onclick: goBack }, "←"),
        h("h2", null, (convById(cid) || {}).name || "Chat")),
      h("div", { class: "page-body" }, UI.errorBox(e, () => loadChat(cid, opts)))));
  }
}
function renderChat() {
  const d = state.detail;
  const c = convById(d.id) || d;
  const header = h("div", { class: "chat-head" },
    h("button", { class: "icon-btn back-btn", type: "button", "aria-label": "Back", onclick: goBack }, "←"),
    convIcon(c),
    h("button", { class: "chat-title", type: "button", onclick: () => chatInfoDialog() },
      h("span", { class: "chat-name ellipsis" }, c.name),
      h("span", { class: "chat-sub ellipsis", id: "chatSub" }, chatSubline(c, d))),
    c.disappearSeconds ? h("button", { class: "chip dis-chip head-extra", type: "button", title: "Disappearing messages", onclick: () => canSetDisappearing(c) ? disappearDialog() : null }, "⏱ " + durationLabel(c.disappearSeconds)) : null,
    h("span", { class: "spacer" }),
    canCallIn(c) ? h("button", { class: "icon-btn", type: "button", title: "Call", "aria-label": "Call " + c.name, onclick: () => startCall(c, "audio") }, "📞") : null,
    canCallIn(c) ? h("button", { class: "icon-btn head-extra", type: "button", title: "Video call", "aria-label": "Video call " + c.name, onclick: () => startCall(c, "video") }, "📹") : null,
    d.folders && d.folders.length ? h("button", { class: "icon-btn head-extra", type: "button", title: "Shared folders", "aria-label": "Shared folders", onclick: (e) => foldersMenu(e.currentTarget) }, "📂") : null,
    h("button", { class: "icon-btn head-extra", type: "button", title: "Search in chat", "aria-label": "Search in chat", onclick: () => searchInChatDialog() }, "🔍"),
    h("button", { class: "icon-btn head-extra", type: "button", title: "Files", "aria-label": "Files", onclick: () => filesDialog(d.id, c.name) }, "📁"),
    h("button", { class: "icon-btn", type: "button", title: "More", "aria-label": "Chat menu", onclick: (e) => chatMenu(e.currentTarget) }, "⋯"));
  const list = h("div", { class: "msg-list", id: "msgList", role: "log", "aria-live": "polite", "aria-relevant": "additions" });
  list.addEventListener("scroll", onListScroll, { passive: true });
  const downBtn = h("button", { class: "to-bottom", id: "toBottom", type: "button", hidden: true, "aria-label": "Newest messages", onclick: () => { state.newBelow = 0; if (state.moreAfter) openChat(state.current); else scrollBottom(true); } }, "↓", h("span", { class: "count", id: "toBottomCount" }));
  // announcements, pins, scheduled messages and My room's note share one strip (paintStrip)
  state.strip = { ann: [], pins: [], sched: [], note: c.kind === "personal" && !lsGet("hchat.roomNoteHidden"), pinAt: 0 };
  const readOnly = c.readOnly ? h("div", { class: "notice read-only" }, "They no longer have access to Household Chat, so this chat is read-only.") : null;
  mount($("#main"), h("section", { class: "chat" }, header, h("div", { id: "chatStrip" }),
    h("div", { class: "msg-wrap" }, list, downBtn),
    h("div", { class: "typing-line", id: "typingLine" }),
    h("div", { class: "select-bar", id: "selectBar", hidden: true }),
    readOnly || composer(c)));
  paintStrip();
  renderPinBar();
  renderMessages();
  renderTyping();
  renderAnnouncements();
  renderScheduledBar();
}
function chatSubline(c, d) {
  const desc = plainText(c.description);
  if (c.kind === "personal") return desc || "Only you can see this in the app";
  if (c.kind === "direct") return [presenceLine(c.otherUserId), desc].filter(Boolean).join(" · ");
  const online = d.members.filter((m) => m.id !== state.me.id && isOnline(m.id)).length;
  return [desc, `${d.members.length} members` + (online ? `, ${online} online` : "")].filter(Boolean).join(" · ");
}
function refreshSubline() { const el = $("#chatSub"); if (el && state.detail) el.textContent = chatSubline(convById(state.detail.id) || state.detail, state.detail); }
function chatMenu(anchor) {
  const c = convById(state.current) || state.detail;
  const d = state.detail;
  const mine = d.members.find((m) => m.id === state.me.id) || {};
  menu(anchor, [
    { label: c.kind === "group" ? "👥 Group info" : "ℹ Info", run: () => chatInfoDialog() },
    canCallIn(c) ? { label: "📹 Video call", run: () => startCall(c, "video") } : null,
    { label: "📌 Pinned messages", run: () => pinsDialog() },
    { label: "📁 Files", run: () => filesDialog(d.id, c.name) },
    { label: "🔍 Search in chat", run: () => searchInChatDialog() },
    c.kind !== "personal" ? { label: "🔔 Notifications", run: () => chatNotifyDialog() } : null,
    canSetDisappearing(c) ? { label: "⏱ Disappearing messages" + (c.disappearSeconds ? ": " + durationLabel(c.disappearSeconds) : ""), run: () => disappearDialog() } : null,
    d.folders && d.folders.length ? { label: "📂 Shared folders", run: () => foldersMenu(anchor) } : null,
    c.kind !== "personal" ? { label: "🕓 Scheduled messages", run: () => scheduledDialog(c.id) } : null,
    { label: "⬇ Download chat…", run: () => exportDialog() },
    c.kind !== "personal" ? { label: c.pinned ? "Unpin chat" : "Pin chat to the top", run: async () => { try { await api(`api/conversations/${c.id}/me`, { method: "PUT", body: { pinned: !c.pinned } }); await loadConvs(); } catch (e) { fail(e); } } } : null,
    c.kind !== "personal" ? "-" : null,
    c.kind === "direct" ? { label: "🗑 Delete chat…", danger: true, run: () => deleteDirectDialog(c) } : null,
    c.kind === "group" ? { label: "Leave group", danger: true, run: () => leaveGroup(c) } : null,
    c.kind === "group" && mine.role === "owner" ? { label: "Delete group…", danger: true, run: () => deleteGroupDialog(c) } : null,
  ]);
}

// ---------- messages ----------
function atBottom() { const l = $("#msgList"); return !l || l.scrollHeight - l.scrollTop - l.clientHeight < 80; }
function scrollBottom(smooth) { const l = $("#msgList"); if (l) l.scrollTo({ top: l.scrollHeight, behavior: smooth ? "smooth" : "auto" }); wasAtBottom = true; updateToBottom(); }
function scrollToMessage(id, flash, alignTop) {
  const el = document.getElementById("m" + id);
  if (!el) return false;
  el.scrollIntoView({ block: alignTop ? "start" : "center" });
  if (flash) { el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1800); }
  return true;
}
function updateToBottom() {
  const b = $("#toBottom");
  if (!b) return;
  const show = !atBottom() || state.moreAfter;
  b.hidden = !show;
  const cnt = $("#toBottomCount");
  if (cnt) cnt.textContent = state.newBelow ? String(state.newBelow) : "";
}
// the newest messages stay in view when the keyboard opens (the page gets shorter) or the composer is tapped
let wasAtBottom = true;
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", () => { if (wasAtBottom && $("#msgList")) scrollBottom(); });
}
async function onListScroll() {
  const l = $("#msgList");
  wasAtBottom = atBottom();
  updateToBottom();
  if (atBottom()) { state.newBelow = 0; markReadSoon(); }
  if (l.scrollTop < 120 && state.moreBefore && !state.loadingOlder && state.msgs.length) {
    state.loadingOlder = true;
    const cid = state.current;
    try {
      const r = await api(`api/conversations/${cid}/messages?before=${state.msgs[0].id}`);
      if (cid !== state.current) return;
      const before = l.scrollHeight;
      state.msgs = r.messages.concat(state.msgs);
      state.moreBefore = r.moreBefore;
      renderMessages();
      l.scrollTop += l.scrollHeight - before;
    } catch (e) { fail(e); } finally { state.loadingOlder = false; }
  }
  if (state.moreAfter && l.scrollHeight - l.scrollTop - l.clientHeight < 120 && !state.loadingNewer) {
    state.loadingNewer = true;
    const cid = state.current;
    try {
      const r = await api(`api/conversations/${cid}/messages?after=${state.msgs[state.msgs.length - 1].id}`);
      if (cid !== state.current) return;
      state.msgs = state.msgs.concat(r.messages.filter((m) => !state.msgs.some((x) => x.id === m.id)));
      state.moreAfter = r.moreAfter;
      renderMessages();
    } catch (e) { fail(e); } finally { state.loadingNewer = false; }
  }
}
function memberNames() {
  const d = state.detail;
  if (!d || d.kind === "personal") return [];
  return d.members.map((m) => m.name.replace(/ \(no longer here\)$/, "")).sort((a, b) => b.length - a.length);
}
function renderMessages() {
  const list = $("#msgList");
  if (!list) return;
  // a disappearing message past its time is gone, even if this page missed the event (e.g. the phone slept)
  const now = Date.now();
  state.msgs = state.msgs.filter((m) => !m.expiresAt || toDate(m.expiresAt) > now);
  const keepBottom = atBottom();
  const ctx = { names: memberNames(), myName: state.me.name };
  const els = [];
  if (!state.moreBefore) {
    const c = convById(state.current) || state.detail;
    els.push(h("div", { class: "chat-start" }, c.kind === "personal" ? "Your personal room. Nothing here is shared." : `This is the start of ${c.kind === "direct" ? "your chat with " + c.name : c.name}.`));
  } else {
    els.push(h("div", { class: "chat-start" }, h("button", { class: "btn small", type: "button", onclick: () => onListScroll() }, "Load earlier messages")));
  }
  let prev = null;
  state.lastMineId = lastMineId();
  const playing = playingVoices(list);
  state.msgs.forEach((m, i) => {
    const d = toDate(m.createdAt);
    if (!prev || !sameDay(toDate(prev.createdAt), d)) els.push(h("div", { class: "day-sep" }, h("span", null, fmtDay(m.createdAt))));
    if (state.unreadMarker && m.id === state.unreadMarker) els.push(h("div", { class: "unread-sep" }, h("span", null, "Unread")));
    els.push(keepPlaying(messageEl(m, groupedAt(i), ctx, state.lastMineId === m.id), playing));
    prev = m;
  });
  mount(list, els);
  if (keepBottom) list.scrollTop = list.scrollHeight;
  updateToBottom();
}
// ---- one message at a time: an event (a reaction, an edit, a vote, a new message at the end) changes only its
// own row, so a long chat isn't drawn again for each, and a voice message that's playing goes on playing ----
function groupedAt(i) {
  const m = state.msgs[i], prev = i > 0 ? state.msgs[i - 1] : null;
  if (!prev) return false;
  const d = toDate(m.createdAt), p = toDate(prev.createdAt);
  return prev.userId === m.userId && !isNote(prev) && !isNote(m) && (d - p) < 5 * 60000 && sameDay(p, d) && !(state.unreadMarker === m.id);
}
function lastMineId() {
  const x = [...state.msgs].reverse().find((m) => m.userId === state.me.id && !m.deleted && !isNote(m));
  return x ? x.id : null;
}
function msgCtx() { return { names: memberNames(), myName: state.me.name }; }
// voice players that are playing inside `root`, by attachment id
function playingVoices(root) {
  const out = new Map();
  if (root) root.querySelectorAll("audio").forEach((a) => { const w = a.closest(".voice[data-att]"); if (w && !a.paused) out.set(w.dataset.att, w); });
  return out;
}
// the new row `el` takes over the playing players (the same element, so the sound doesn't stop)
function keepPlaying(el, playing) {
  if (!playing.size) return el;
  el.querySelectorAll(".voice[data-att]").forEach((w) => { const p = playing.get(w.dataset.att); if (p) w.replaceWith(p); });
  return el;
}
function patchMessage(m) {
  const list = $("#msgList");
  const old = document.getElementById("m" + m.id);
  const i = state.msgs.findIndex((x) => x.id === m.id);
  if (!list || !old || i < 0 || lastMineId() !== state.lastMineId || (m.expiresAt && toDate(m.expiresAt) <= Date.now())) { renderMessages(); return; }
  const keepBottom = atBottom();
  old.replaceWith(keepPlaying(messageEl(m, groupedAt(i), msgCtx(), state.lastMineId === m.id), playingVoices(old)));
  if (keepBottom) list.scrollTop = list.scrollHeight;
  updateToBottom();
}
function appendMessage(m) {
  const list = $("#msgList");
  const i = state.msgs.length - 1;
  const prev = i > 0 ? state.msgs[i - 1] : null;
  if (!list || state.msgs[i] !== m || (prev && !document.getElementById("m" + prev.id)) || state.unreadMarker === m.id) { renderMessages(); return; }
  const keepBottom = atBottom();
  const before = state.lastMineId;
  state.lastMineId = lastMineId();
  if (!prev || !sameDay(toDate(prev.createdAt), toDate(m.createdAt))) list.appendChild(h("div", { class: "day-sep" }, h("span", null, fmtDay(m.createdAt))));
  list.appendChild(messageEl(m, groupedAt(i), msgCtx(), state.lastMineId === m.id));
  if (before !== state.lastMineId && before != null) {           // "Seen" moves to my newest message
    const pm = state.msgs.find((x) => x.id === before);
    if (pm) patchMessage(pm);
  }
  if (keepBottom) list.scrollTop = list.scrollHeight;
  updateToBottom();
}
function seenBy(m) {
  const c = convById(state.current) || state.detail;
  if (c.kind === "personal") return null;
  const readers = state.reads.filter((r) => r.userId !== state.me.id && r.lastReadId >= m.id).map((r) => nameOf(r.userId));
  if (!readers.length) return null;
  if (c.kind === "direct") return "Seen";
  return "Seen by " + (readers.length > 4 ? readers.slice(0, 4).join(", ") + ` +${readers.length - 4}` : readers.join(", "));
}
// system lines and call notes sit in the middle of the chat, without a bubble
function isNote(m) { return m.kind === "system" || m.kind === "call"; }
function messageEl(m, grouped, ctx, isLastMine) {
  if (m.kind === "system") return h("div", { class: "sys", id: "m" + m.id }, h("span", null, m.body, " · ", fmtTime(m.createdAt)));
  if (m.kind === "call") return callNoteEl(m);
  const mine = m.userId === state.me.id;
  const c = convById(state.current) || state.detail;
  const bubble = h("div", { class: "bubble" + (m.deleted ? " deleted" : "") + (m.announcement ? " announcement" : "") });
  if (!mine && !grouped && c.kind === "group") bubble.appendChild(h("div", { class: "author " + colorOf(m.author) }, m.author));
  if (m.forwarded) bubble.appendChild(h("div", { class: "fwd" }, "↪ Forwarded"));
  if (m.replyTo) bubble.appendChild(replyQuote(m.replyTo));
  if (m.deleted) bubble.appendChild(h("div", { class: "fmt" }, h("em", null, "Message deleted")));
  else {
    if (m.kind === "poll" && m.poll) bubble.appendChild(pollEl(m));
    if (m.kind === "card" && m.card) bubble.appendChild(cardEl(m));
    if (m.attachments.length) bubble.appendChild(attachmentsEl(m));
    if (m.body && m.kind !== "poll" && m.kind !== "card") bubble.appendChild(renderText(m.body, ctx));
    if (m.announcement) bubble.appendChild(announcementBlock(m));
  }
  const meta = h("div", { class: "meta" },
    m.expiresAt ? h("span", { class: "expiry", title: "Disappears " + fmtFull(m.expiresAt) }, "⏱ " + timeLeft(m.expiresAt)) : null,
    m.pinned ? h("span", { title: "Pinned" }, "📌") : null,
    m.starred ? h("span", { title: "Starred" }, "★") : null,
    m.reminder ? h("span", { title: "Reminder " + fmtFull(m.reminder.dueAt) }, "⏰") : null,
    m.via === "notification" ? h("span", { title: "Replied from a phone notification" }, "📱") : null,
    m.editedAt && !m.deleted ? h("span", null, "edited") : null,
    h("span", { title: fmtFull(m.createdAt) }, fmtTime(m.createdAt)));
  bubble.appendChild(meta);
  const selectable = state.selecting && !m.deleted && !m.expiresAt;
  const row = h("div", { class: "msg" + (mine ? " mine" : "") + (grouped ? " grouped" : "") + (selectable ? " selectable" : "") + (selectable && state.selecting.has(m.id) ? " selected" : "") + (m.mentions.includes(state.me.id) || (m.mentionAll && !mine) ? " at-me" : ""), id: "m" + m.id },
    !mine && c.kind === "group" ? (grouped ? h("span", { class: "avatar-space" }) : avatar(m.author, m.userId, { small: true })) : null,
    h("div", { class: "msg-col" }, bubble,
      m.reactions.length ? h("div", { class: "reactions" }, m.reactions.map((r) => h("button", {
        class: "reaction" + (r.userIds.includes(state.me.id) ? " mine" : ""), type: "button", title: r.userIds.map(nameOf).join(", "),
        onclick: () => toggleReaction(m, r.emoji),
      }, r.emoji, " ", String(r.userIds.length)))) : null,
      isLastMine && seenBy(m) ? h("div", { class: "seen" }, seenBy(m)) : null),
    !m.deleted ? h("button", { class: "icon-btn msg-more", type: "button", "aria-label": "Message actions", onclick: (e) => messageMenu(m, e.currentTarget) }, "⋯") : null);
  if (selectable) {
    row.insertBefore(h("input", { type: "checkbox", class: "sel-box", checked: state.selecting.has(m.id), "aria-label": "Select message", tabindex: "-1" }), row.firstChild);
    row.addEventListener("click", (e) => { if (e.target.closest("a, audio, .poll-opt, .reaction, .quote")) return; e.preventDefault(); toggleSelected(m.id); });
    return row;
  }
  if (!m.deleted) {
    row.addEventListener("contextmenu", (e) => { if (e.target.closest("a, audio, input")) return; e.preventDefault(); messageMenu(m, null, { x: e.clientX, y: e.clientY }); });
    let timer = null;
    row.addEventListener("touchstart", (e) => {
      if (e.target.closest("a, button, audio, input")) return;
      const t = e.touches[0];
      timer = setTimeout(() => { timer = null; if (navigator.vibrate) navigator.vibrate(10); messageMenu(m, null, { x: t.clientX, y: t.clientY }); }, 500);
    }, { passive: true });
    ["touchend", "touchmove", "touchcancel"].forEach((ev) => row.addEventListener(ev, () => { if (timer) { clearTimeout(timer); timer = null; } }, { passive: true }));
  }
  return row;
}
function replyQuote(r) {
  if (r.gone) return h("div", { class: "quote" }, h("span", { class: "hint" }, "Original message no longer available"));
  if (r.hidden) return h("div", { class: "quote" }, h("span", { class: "hint" }, "Replying to an earlier message"));
  const text = r.deleted ? "Message deleted" : r.kind === "card" ? "📄 " + r.text : r.kind === "call" ? "📞 Call" : (r.text || (r.kind === "poll" ? "📊 Poll" : r.files ? "📎 File" : ""));
  return h("button", { class: "quote", type: "button", onclick: () => jumpTo(r.id) },
    h("span", { class: "quote-author" }, r.userId === state.me.id ? "You" : (r.author || "")), h("span", { class: "quote-text" }, text));
}
async function jumpTo(id) {
  if (scrollToMessage(id, true)) return;
  await openChat(state.current, { around: id });
}
function canEdit(m) {
  const win = state.me.app.editWindowMinutes;
  const c = convById(state.current) || state.detail;
  return m.userId === state.me.id && (m.kind === "text" || m.kind === "file") && !m.deleted && win > 0 && !c.readOnly &&
    (Date.now() - toDate(m.createdAt)) < win * 60000;
}
function myRole() { const d = state.detail; const me = d && d.members.find((x) => x.id === state.me.id); return me ? me.role : "member"; }
function messageMenu(m, anchor, at) {
  const c = convById(state.current) || state.detail;
  const manager = c.kind === "group" && ["owner", "admin"].includes(myRole()) && !isChild();
  const quick = c.kind !== "personal" && !c.readOnly ? { row: h("div", { class: "quick-react" }, QUICK_REACTIONS.map((e) => h("button", { type: "button", class: "icon-btn", "aria-label": "React " + e, onclick: () => { closeMenus(); toggleReaction(m, e); } }, e)),
    h("button", { type: "button", class: "icon-btn", "aria-label": "More reactions", onclick: () => { closeMenus(); emojiPicker(null, (e) => toggleReaction(m, e), at || anchor); } }, "＋")) } : null;
  menu(anchor, [
    quick,
    !c.readOnly ? { label: "↩ Reply", run: () => startReply(m) } : null,
    m.body && m.kind !== "poll" && m.kind !== "card" ? { label: "📋 Copy text", run: () => copyText(m.body) } : null,
    canEdit(m) ? { label: "✏ Edit", run: () => startEdit(m) } : null,
    !c.readOnly && !isChild() ? { label: m.pinned ? "📌 Unpin" : "📌 Pin", run: () => togglePin(m) } : null,
    { label: m.starred ? "★ Unstar" : "☆ Star", run: () => toggleStar(m) },
    { label: m.reminder ? "⏰ Cancel reminder" : "⏰ Remind me…", run: () => m.reminder ? cancelReminder(m) : remindDialog(m) },
    m.kind !== "system" && m.kind !== "card" && !m.expiresAt ? { label: "↪ Forward…", run: () => forwardDialog([m.id]) } : null,
    m.kind !== "system" && !m.expiresAt ? { label: "☑ Select messages…", run: () => startSelecting(m) } : null,
    (m.userId === state.me.id || manager) ? "-" : null,
    (m.userId === state.me.id || manager) ? { label: "🗑 Delete", danger: true, run: () => deleteMessage(m) } : null,
  ], at);
}
function replaceMessage(nm) {
  const i = state.msgs.findIndex((x) => x.id === nm.id);
  if (i < 0) return false;
  const old = state.msgs[i];
  // stars, reminders and "heard" are mine only; events from others don't carry them
  if (nm.starred === false && old.starred && nm._fromEvent) nm.starred = true;
  if (!nm.reminder && old.reminder && nm._fromEvent) nm.reminder = old.reminder;
  if (nm._fromEvent) nm.attachments.forEach((a) => { const o = old.attachments.find((x) => x.id === a.id); if (o) a.heard = o.heard; });
  state.msgs[i] = nm;
  return true;
}
async function msgAction(p, opts) { try { const nm = await api(p, opts); if (replaceMessage(nm)) patchMessage(nm); else renderMessages(); return nm; } catch (e) { fail(e); return null; } }
function toggleReaction(m, e) {
  const cur = state.msgs.find((x) => x.id === m.id) || m;
  const mine = cur.reactions.some((r) => r.emoji === e && r.userIds.includes(state.me.id));
  msgAction(`api/messages/${m.id}/reactions/${encodeURIComponent(e)}`, { method: mine ? "DELETE" : "PUT" });
}
function togglePin(m) { msgAction(`api/messages/${m.id}/pin`, { method: m.pinned ? "DELETE" : "PUT" }).then(() => renderPinBar()); }
function toggleStar(m) { msgAction(`api/messages/${m.id}/star`, { method: m.starred ? "DELETE" : "PUT" }).then((r) => { if (r) toast(r.starred ? "Starred" : "Unstarred"); }); }
function cancelReminder(m) { msgAction(`api/messages/${m.id}/remind`, { method: "DELETE" }).then(() => toast("Reminder cancelled")); }
async function deleteMessage(m) {
  if (!await confirmDialog("Delete message", m.userId === state.me.id ? "Delete this message for everyone?" : `Delete ${m.author}'s message for everyone?`, "Delete", true)) return;
  await msgAction(`api/messages/${m.id}`, { method: "DELETE" });
  renderPinBar();
}

// ---------- selecting several messages (to forward them together) ----------
function startSelecting(m) {
  state.selecting = new Set([m.id]);
  renderMessages(); renderSelectBar();
}
function toggleSelected(id) {
  if (!state.selecting) return;
  if (state.selecting.has(id)) state.selecting.delete(id); else if (state.selecting.size < 20) state.selecting.add(id);
  else toast("At most 20 at once.", { error: true });
  renderMessages(); renderSelectBar();
}
function stopSelecting() { state.selecting = null; renderMessages(); renderSelectBar(); }
function renderSelectBar() {
  const bar = $("#selectBar");
  const comp = $("#composer");
  if (!bar) return;
  const on = !!state.selecting;
  bar.hidden = !on;
  if (comp) comp.hidden = on;
  if (!on) { clear(bar); return; }
  const n = state.selecting.size;
  mount(bar, h("span", { class: "grow" }, `${n} selected`),
    h("button", { class: "btn", type: "button", onclick: stopSelecting }, "Cancel"),
    h("button", { class: "btn primary", type: "button", disabled: !n, onclick: () => { const ids = [...state.selecting].sort((a, b) => a - b); stopSelecting(); forwardDialog(ids); } }, "↪ Forward"));
}

// ---------- the strip under the chat's header: announcements, pins, scheduled messages, My room's note ----------
// One strip: the first item shows (an announcement waiting for you comes first), "+N ▾" opens the others.
async function renderPinBar() {
  if (!state.current) return;
  const cid = state.current;
  let pins = [];
  try { pins = (await api(`api/conversations/${cid}/pins`)).messages; } catch (e) { return; }
  if (cid !== state.current) return;
  state.pins = pins;
  state.strip.pins = pins;
  if (state.strip.pinAt >= pins.length) state.strip.pinAt = 0;
  paintStrip();
}
function pinText(p) {
  return (p.kind === "poll" ? "📊 " + p.poll.question : p.kind === "card" ? (p.card ? (CARD_ICONS[p.card.type] || "📄") + " " + p.card.title : p.body) : plainText(p.body) || (p.attachments[0] ? "📎 " + p.attachments[0].name : "")) || "…";
}
function stripRows() {
  const st = state.strip;
  const rows = [];
  const ann = [...st.ann].sort((a, b) => b.announcement.pending.includes(state.me.id) - a.announcement.pending.includes(state.me.id));
  for (const a of ann) rows.push(annRow(a));
  if (st.pins.length) {
    const pins = st.pins;
    rows.push(h("div", { class: "strip-row pin" },
      h("button", { class: "pin-main", type: "button", title: "Go to the pinned message", onclick: () => {
        jumpTo(pins[st.pinAt].id);
        if (pins.length > 1) { st.pinAt = (st.pinAt + 1) % pins.length; paintStrip(); }
      } }, h("span", null, "📌"), h("span", { class: "pin-text ellipsis" }, pinText(pins[st.pinAt]))),
      pins.length > 1 ? h("span", { class: "hint", id: "pinCount" }, `${st.pinAt + 1}/${pins.length}`) : null,
      h("button", { class: "icon-btn", type: "button", title: "All pinned messages", "aria-label": "All pinned messages", onclick: () => pinsDialog() }, "☰")));
  }
  if (st.sched.length) {
    rows.push(h("div", { class: "strip-row sched" }, h("button", { class: "pin-main", type: "button", onclick: () => scheduledDialog(state.current) },
      h("span", null, "🕓"), h("span", { class: "grow ellipsis" }, `${st.sched.length} scheduled — next ${fmtFull(st.sched[0].sendAt)}`))));
  }
  if (st.note) {
    rows.push(h("div", { class: "strip-row note" },
      h("span", { class: "grow" }, "📌 Only you can see this in the app — notes, links and documents for yourself. It isn't encrypted. ",
        h("button", { class: "link-btn", type: "button", onclick: () => showPage("settings") }, "Who can see what")),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Hide this note", title: "Hide this note", onclick: () => { lsSet("hchat.roomNoteHidden", "1"); st.note = false; paintStrip(); } }, "✕")));
  }
  return rows;
}
function paintStrip() {
  const bar = $("#chatStrip");
  if (!bar) return;
  const rows = stripRows();
  if (!rows.length) { clear(bar); bar.className = ""; return; }
  const open = state.stripOpen && rows.length > 1;
  bar.className = "chat-strip" + (open ? " open" : "");
  mount(bar, h("div", { class: "strip-rows" }, open ? rows : rows[0]),
    rows.length > 1 ? h("button", { class: "icon-btn strip-more", type: "button", "aria-expanded": String(open),
      "aria-label": open ? "Show less" : `Show ${rows.length - 1} more`, title: open ? "Show less" : "Show the others",
      onclick: () => { state.stripOpen = !open; paintStrip(); } }, open ? "▴" : `+${rows.length - 1} ▾`) : null);
}

// ---------- attachments ----------
function fileIcon(a) {
  if (a.voice) return "🎤";
  if (a.image) return "🖼";
  if (a.mime === "application/pdf") return "📄";
  if (a.mime.startsWith("audio/")) return "🎵";
  if (a.mime.startsWith("video/")) return "🎬";
  if (a.mime.startsWith("text/")) return "📝";
  if (/sheet|excel/.test(a.mime)) return "📊";
  if (/word|document/.test(a.mime)) return "📃";
  if (/zip/.test(a.mime)) return "🗜";
  return "📎";
}
function attachmentsEl(m) {
  const box = h("div", { class: "atts" });
  const images = m.attachments.filter((a) => a.image && !a.missing && !a.adminDeleted);
  if (images.length) {
    box.appendChild(h("div", { class: "img-grid n" + Math.min(images.length, 4) }, images.map((a, k) => h("button", { class: "img-btn", type: "button", "aria-label": "Open " + a.name, onclick: () => viewer(images, k) },
      h("img", { src: `api/files/${a.id}/thumb`, alt: a.name, loading: "lazy", width: a.width && a.height ? Math.min(320, a.width) : null })))));
  }
  for (const a of m.attachments) {
    if (a.image && !a.missing && !a.adminDeleted) continue;
    if (a.adminDeleted) { box.appendChild(h("div", { class: "file-chip missing" }, "🗑 ", h("span", { class: "hint" }, "File deleted by admin"))); continue; }
    if (a.missing) { box.appendChild(h("div", { class: "file-chip missing" }, "⚠ ", a.name, h("span", { class: "hint" }, " — file no longer available"))); continue; }
    if (a.voice || (a.mime.startsWith("audio/") && ["audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg"].includes(a.mime))) { box.appendChild(voicePlayer(a)); continue; }
    const viewable = a.mime === "application/pdf" || a.mime === "text/plain";
    box.appendChild(h("div", { class: "file-chip" },
      h("span", { class: "file-icon" }, fileIcon(a)),
      h("span", { class: "grow" }, viewable ? h("button", { class: "link-btn", type: "button", onclick: () => viewer([a], 0) }, a.name) : h("span", null, a.name),
        h("span", { class: "hint" }, " · " + fmtSize(a.size))),
      h("a", { class: "icon-btn", href: `api/files/${a.id}?download=1`, download: a.name, title: "Download", "aria-label": "Download " + a.name }, "⬇")));
  }
  return box;
}
function voicePlayer(a) {
  const audio = h("audio", { preload: "none", src: `api/files/${a.id}` });
  const btn = h("button", { class: "icon-btn play", type: "button", "aria-label": "Play" }, "▶");
  const bar = h("input", { type: "range", min: "0", max: "1000", value: "0", "aria-label": "Position", class: "voice-bar" });
  const time = h("span", { class: "hint mono" }, fmtDuration(a.duration));
  const speeds = [1, 1.5, 2];
  let si = 0;
  const speed = h("button", { class: "btn small ghost", type: "button", title: "Speed" }, "1×");
  let heard = a.heard;
  btn.addEventListener("click", () => {
    document.querySelectorAll("audio").forEach((x) => { if (x !== audio) x.pause(); });
    if (audio.paused) audio.play().catch(() => toast("This browser can't play that recording.", { error: true })); else audio.pause();
  });
  audio.addEventListener("play", () => {
    btn.textContent = "⏸";
    if (!heard && a.voice && a.uploadedBy !== state.me.id) { heard = true; wrap.classList.remove("unheard"); api(`api/files/${a.id}/heard`, { method: "POST" }).catch(() => {}); }
  });
  audio.addEventListener("pause", () => { btn.textContent = "▶"; });
  audio.addEventListener("ended", () => { btn.textContent = "▶"; bar.value = "0"; });
  audio.addEventListener("timeupdate", () => {
    const dur = isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (a.duration || 1);
    bar.value = String(Math.round(audio.currentTime / dur * 1000));
    time.textContent = fmtDuration(audio.currentTime) + " / " + fmtDuration(dur);
  });
  bar.addEventListener("input", () => {
    const dur = isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (a.duration || 0);
    if (dur) audio.currentTime = bar.value / 1000 * dur;
  });
  speed.addEventListener("click", () => { si = (si + 1) % speeds.length; audio.playbackRate = speeds[si]; speed.textContent = speeds[si] + "×"; });
  const wrap = h("div", { class: "voice" + (a.voice && heard === false && a.uploadedBy !== state.me.id ? " unheard" : ""), "data-att": a.id }, btn, bar, time, speed, audio,
    a.voice ? null : h("span", { class: "hint ellipsis" }, a.name));
  return wrap;
}

// ---------- cards shared from other household apps (SPEC §15.11) ----------
// Only a title, type, owner and a link: never the document itself. Whether someone may open it is the other
// app's business (Docs shows its own "No access" page).
const CARD_ICONS = { note: "📝", checklist: "✅", sheet: "🧮", folder: "📁", file: "📄" };
const CARD_TYPES = { note: "Note", checklist: "Checklist", sheet: "Sheet", folder: "Folder", file: "File" };
function cardEl(m) {
  const c = m.card;
  const who = m.userId === state.me.id ? "You" : (m.author || "Someone");
  const app = c.badge || "the other app";
  const href = typeof c.href === "string" && /^\/[a-z0-9]{1,16}_[a-z0-9_]{1,40}(\/(doc|folder|file)\/[A-Za-z0-9_-]{1,64})?$/.test(c.href) ? c.href : null;
  const open = href
    ? h("a", { class: "btn small app-card-open", href, target: "_top", rel: "noopener",
      onclick: (e) => { if (e.ctrlKey || e.metaKey || e.shiftKey || e.button) return; e.preventDefault(); ConnectedApps.openAppPage(href); } }, "Open in " + app)
    : h("div", { class: "hint app-card-open" }, `Open the ${app} app from the sidebar to see it.`);
  return h("div", { class: "app-card" },
    h("div", { class: "app-card-icon", "aria-hidden": "true" }, CARD_ICONS[c.type] || "📄"),
    h("div", { class: "app-card-body" },
      h("div", { class: "app-card-title" }, who + " shared ", h("strong", null, c.title)),
      h("div", { class: "hint" }, [CARD_TYPES[c.type] || "Document", c.owner ? c.owner + "'s" : null, "from " + app].filter(Boolean).join(" · ")),
      c.sharedWithMembers ? h("div", { class: "hint" }, "Shared with this chat's members") : null,
      open));
}

// ---------- polls ----------
function pollEl(m) {
  const p = m.poll;
  const voters = new Set(p.options.flatMap((o) => o.votes));
  const total = voters.size;
  const mineSel = new Set(p.options.filter((o) => o.votes.includes(state.me.id)).map((o) => o.id));
  const c = convById(state.current) || state.detail;
  const canClose = !p.closed && (m.userId === state.me.id || (c.kind === "group" && ["owner", "admin"].includes(myRole())));
  const vote = async (oid) => {
    if (p.closed || c.readOnly) return;
    let ids;
    if (p.multi) { ids = new Set(mineSel); if (ids.has(oid)) ids.delete(oid); else ids.add(oid); ids = [...ids]; }
    else ids = mineSel.has(oid) ? [] : [oid];
    await msgAction(`api/polls/${m.id}/vote`, { method: "PUT", body: { optionIds: ids } });
  };
  return h("div", { class: "poll" },
    h("div", { class: "poll-q" }, "📊 ", m.poll.question),
    h("div", { class: "hint" }, (p.multi ? "Choose one or more" : "Choose one") + (p.closed ? " · closed" : p.closesAt ? " · closes " + fmtFull(p.closesAt) : "")),
    p.options.map((o) => {
      const pct = total ? Math.round(o.votes.length / total * 100) : 0;
      return h("button", { class: "poll-opt" + (mineSel.has(o.id) ? " mine" : ""), type: "button", disabled: p.closed || c.readOnly, onclick: () => vote(o.id), title: o.votes.map(nameOf).join(", ") },
        (() => { const f = h("span", { class: "poll-fill" }); f.style.width = pct + "%"; return f; })(),
        h("span", { class: "poll-check" }, mineSel.has(o.id) ? (p.multi ? "☑" : "◉") : (p.multi ? "☐" : "○")),
        h("span", { class: "grow poll-text" }, o.text),
        h("span", { class: "poll-count" }, String(o.votes.length)),
        o.votes.length ? h("span", { class: "poll-who hint" }, o.votes.map(nameOf).join(", ")) : null);
    }),
    h("div", { class: "row" }, h("span", { class: "hint" }, `${total} voted`), h("span", { class: "spacer" }),
      canClose ? h("button", { class: "btn small", type: "button", onclick: async () => { if (await confirmDialog("Close poll", "Nobody can vote after this.", "Close poll")) msgAction(`api/polls/${m.id}/close`, { method: "POST" }); } }, "Close poll") : null));
}
// ---------- typing ----------
function typingText(cid) {
  const t = state.typing[cid];
  if (!t) return "";
  const now = Date.now();
  const names = Object.values(t).filter((x) => x.until > now).map((x) => x.name);
  if (!names.length) return "";
  return names.length === 1 ? `${names[0]} is typing…` : names.length === 2 ? `${names[0]} and ${names[1]} are typing…` : "Several people are typing…";
}
function renderTyping() { const el = $("#typingLine"); if (el) el.textContent = state.current ? typingText(state.current) : ""; }

// ---------- reading ----------
const markReadSoon = debounce(async () => {
  const cid = state.current;
  if (!cid || state.page !== "chat" || document.visibilityState !== "visible" || !atBottom() || state.moreAfter) return;
  const last = state.msgs[state.msgs.length - 1];
  const c = convById(cid);
  if (!last || !c || (c.lastReadId || 0) >= last.id) return;
  try {
    await api(`api/conversations/${cid}/read`, { method: "POST", body: { upTo: last.id } });
    c.lastReadId = last.id; c.unread = 0; c.mentionUnread = 0;
    renderConvList();
  } catch (e) { /* ignore */ }
}, 400);
function sendPresence() {
  const visible = document.visibilityState === "visible" && document.hasFocus();
  api("api/presence", { method: "POST", body: { tab: TAB_ID, conversationId: state.page === "chat" ? state.current : null, visible } }).catch(() => {});
}
