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
            onkeydown: (e) => { if (e.key === "Enter" && e.target.value.trim()) showPage("search", e.target.value.trim()); } })),
        h("div", { id: "filesBanner" }),
        h("div", { class: "conv-list", id: "convList", role: "list" }),
        h("div", { class: "side-foot" },
          avatar(state.me.name, state.me.id, { small: true, noDot: true }),
          h("span", { class: "grow ellipsis" }, state.me.name),
          h("span", { class: "conn-dot", id: "connDot", title: "Live updates" }),
          h("button", { class: "icon-btn", type: "button", title: "Settings", "aria-label": "Settings", onclick: () => showPage("settings") }, "⚙"))),
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
// On a phone the list and the open chat (or page) are one pane at a time; the chat is a layer, so the
// back gesture returns to the list instead of leaving the app.
const PHONE = window.matchMedia ? window.matchMedia("(max-width: 760px)") : { matches: false };
let mainLayer = null;
function setView(v) {
  const s = $("#shell");
  if (s) s.setAttribute("data-view", v);
  if (v === "main" && PHONE.matches && !mainLayer) mainLayer = addLayer(backFromHistory);
  else if (v === "list" && mainLayer) { const l = mainLayer; mainLayer = null; removeLayer(l); }
}
function backToList() { saveDraft(); state.current = null; state.page = "chat"; renderConvList(); setView("list"); sendPresence(); }
function backFromHistory() { mainLayer = null; backToList(); }
function newChatMenu(anchor) {
  menu(anchor, [
    { label: "💬 Direct message", run: () => newDirectDialog() },
    !isChild() && (state.me.app.whoCanCreateGroups === "everyone" || state.me.isAdmin) ? { label: "👥 New group", run: () => newGroupDialog() } : null,
  ]);
}
function mainMenu(anchor) {
  menu(anchor, [
    { label: "📌 My room", run: () => openChat(state.me.personalRoomId) },
    { label: "☆ Starred", run: () => showPage("starred") },
    { label: "⏰ Reminders", run: () => showPage("reminders") },
    callsOn() ? { label: "📞 Calls", run: () => showPage("calls") } : null,
    { label: "🕓 Scheduled", run: () => scheduledDialog(null) },
    { label: "📁 My files", run: () => showPage("files") },
    "-",
    { label: "⚙ Settings", run: () => showPage("settings") },
    state.me.isAdmin ? { label: "🛡️ Admin", run: () => showPage("admin") } : null,
    { label: "👤 How the app sees you", run: () => showPage("whoami") },
  ]);
}

// ---------- chat list ----------
function renderConvList() {
  const box = $("#convList");
  if (!box) return;
  const rows = state.convs.map((c) => {
    const last = c.lastMessage;
    const typing = typingText(c.id);
    const draft = !typing && c.draft && c.draft.body && c.id !== state.current ? c.draft.body : null;
    let preview = typing || (last ? (last.by && last.by !== state.me.id && c.kind === "group" ? `${last.author}: ` : last.by === state.me.id && c.kind !== "personal" ? "You: " : "") + last.preview : (c.kind === "personal" ? "Only you can see this in the app" : ""));
    const sub = c.kind === "direct" && c.otherUserId ? (state.homeAway[c.otherUserId] || {}).label : null;
    return h("button", {
      class: "conv" + (c.id === state.current && state.page === "chat" ? " active" : "") + (c.unread ? " unread" : ""), type: "button", role: "listitem",
      onclick: () => openChat(c.id), "aria-label": `${c.name}${c.unread ? `, ${c.unread} unread` : ""}`,
    },
      convIcon(c),
      h("span", { class: "conv-main" },
        h("span", { class: "conv-top" },
          h("span", { class: "conv-name ellipsis" }, c.name),
          c.pinned && c.kind !== "personal" ? h("span", { class: "hint", title: "Pinned" }, "📌") : null,
          c.muted ? h("span", { class: "hint", title: "Muted" }, "🔕") : null,
          c.disappearSeconds ? h("span", { class: "hint", title: "Disappearing messages: " + durationLabel(c.disappearSeconds) }, "⏱") : null,
          h("span", { class: "conv-time" }, last ? fmtWhen(last.at) : "")),
        sub ? h("span", { class: "conv-presence ellipsis" }, sub) : null,
        h("span", { class: "conv-bottom" },
          draft ? h("span", { class: "conv-preview ellipsis" }, h("span", { class: "draft-lbl" }, "Draft: "), plainText(draft))
            : h("span", { class: "conv-preview ellipsis" + (typing ? " typing" : "") }, preview),
          c.mentionUnread ? h("span", { class: "badge at", title: "Mentions you" }, "@") : null,
          c.unread ? h("span", { class: "badge" + (c.muted ? " muted" : "") }, c.unread > 99 ? "99+" : String(c.unread)) : null)));
  });
  mount(box, rows);
  updateTitle();
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
    state.current = null;
    if (state.page === "chat") renderEmptyMain();
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
async function openChat(cid, opts = {}) {
  if (!cid) return;
  saveDraft();
  state.page = "chat";
  state.current = cid;
  state.editing = null;
  state.selecting = null;
  state.newBelow = 0;
  setView("main");
  renderConvList();
  mount($("#main"), h("div", { class: "loading" }, "Loading…"));
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
    fail(e);
    if (e.status === 404) { state.current = null; await loadConvs(); renderEmptyMain(); }
  }
}
function renderChat() {
  const d = state.detail;
  const c = convById(d.id) || d;
  const header = h("div", { class: "chat-head" },
    h("button", { class: "icon-btn back-btn", type: "button", "aria-label": "Back", onclick: backToList }, "←"),
    convIcon(c),
    h("button", { class: "chat-title", type: "button", onclick: () => chatInfoDialog() },
      h("span", { class: "chat-name ellipsis" }, c.name),
      h("span", { class: "chat-sub ellipsis", id: "chatSub" }, chatSubline(c, d))),
    c.disappearSeconds ? h("button", { class: "chip dis-chip", type: "button", title: "Disappearing messages", onclick: () => canSetDisappearing(c) ? disappearDialog() : null }, "⏱ " + durationLabel(c.disappearSeconds)) : null,
    h("span", { class: "spacer" }),
    canCallIn(c) ? h("button", { class: "icon-btn", type: "button", title: "Call", "aria-label": "Call " + c.name, onclick: () => startCall(c) }, "📞") : null,
    d.folders && d.folders.length ? h("button", { class: "icon-btn", type: "button", title: "Shared folders", "aria-label": "Shared folders", onclick: (e) => foldersMenu(e.currentTarget) }, "📂") : null,
    h("button", { class: "icon-btn", type: "button", title: "Search in chat", "aria-label": "Search in chat", onclick: () => searchInChatDialog() }, "🔍"),
    h("button", { class: "icon-btn", type: "button", title: "Files", "aria-label": "Files", onclick: () => filesDialog(d.id, c.name) }, "📁"),
    h("button", { class: "icon-btn", type: "button", title: "More", "aria-label": "Chat menu", onclick: (e) => chatMenu(e.currentTarget) }, "⋯"));
  const list = h("div", { class: "msg-list", id: "msgList", role: "log", "aria-live": "polite", "aria-relevant": "additions" });
  list.addEventListener("scroll", onListScroll, { passive: true });
  const downBtn = h("button", { class: "to-bottom", id: "toBottom", type: "button", hidden: true, "aria-label": "Newest messages", onclick: () => { state.newBelow = 0; if (state.moreAfter) openChat(state.current); else scrollBottom(true); } }, "↓", h("span", { class: "count", id: "toBottomCount" }));
  const note = c.kind === "personal" ? h("div", { class: "room-note" }, "📌 Only you can see this in the app — notes, links and documents for yourself. Like every chat, it isn't encrypted: whoever can get into the Home Assistant machine or its backups can read it.") : null;
  const readOnly = c.readOnly ? h("div", { class: "notice read-only" }, "They no longer have access to Household Chat, so this chat is read-only.") : null;
  mount($("#main"), h("section", { class: "chat" }, header, h("div", { id: "annBar" }), h("div", { id: "pinBar" }), h("div", { id: "schedBar" }), note,
    h("div", { class: "msg-wrap" }, list, downBtn),
    h("div", { class: "typing-line", id: "typingLine" }),
    h("div", { class: "select-bar", id: "selectBar", hidden: true }),
    readOnly || composer(c)));
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
    { label: "📌 Pinned messages", run: () => pinsDialog() },
    { label: "📁 Files", run: () => filesDialog(d.id, c.name) },
    { label: "🔍 Search in chat", run: () => searchInChatDialog() },
    c.kind !== "personal" ? { label: "🔔 Notifications", run: () => chatNotifyDialog() } : null,
    canSetDisappearing(c) ? { label: "⏱ Disappearing messages" + (c.disappearSeconds ? ": " + durationLabel(c.disappearSeconds) : ""), run: () => disappearDialog() } : null,
    d.folders && d.folders.length ? { label: "📂 Shared folders", run: () => foldersMenu(anchor) } : null,
    c.kind !== "personal" ? { label: "🕓 Scheduled messages", run: () => scheduledDialog(c.id) } : null,
    { label: "⬇ Download chat…", run: () => exportDialog() },
    c.kind !== "personal" ? { label: c.pinned ? "Unpin chat" : "Pin chat to the top", run: async () => { try { await api(`api/conversations/${c.id}/me`, { method: "PUT", body: { pinned: !c.pinned } }); await loadConvs(); } catch (e) { fail(e); } } } : null,
    c.kind === "group" ? "-" : null,
    c.kind === "group" ? { label: "Leave group", danger: true, run: () => leaveGroup(c) } : null,
    c.kind === "group" && mine.role === "owner" ? { label: "Delete group…", danger: true, run: () => deleteGroupDialog(c) } : null,
  ]);
}

// ---------- messages ----------
function atBottom() { const l = $("#msgList"); return !l || l.scrollHeight - l.scrollTop - l.clientHeight < 80; }
function scrollBottom(smooth) { const l = $("#msgList"); if (l) l.scrollTo({ top: l.scrollHeight, behavior: smooth ? "smooth" : "auto" }); updateToBottom(); }
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
async function onListScroll() {
  const l = $("#msgList");
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
  const lastMine = [...state.msgs].reverse().find((m) => m.userId === state.me.id && !m.deleted && !isNote(m));
  for (const m of state.msgs) {
    const d = toDate(m.createdAt);
    if (!prev || !sameDay(toDate(prev.createdAt), d)) els.push(h("div", { class: "day-sep" }, h("span", null, fmtDay(m.createdAt))));
    if (state.unreadMarker && m.id === state.unreadMarker) els.push(h("div", { class: "unread-sep" }, h("span", null, "Unread")));
    const grouped = prev && prev.userId === m.userId && !isNote(prev) && !isNote(m) && (d - toDate(prev.createdAt)) < 5 * 60000 && sameDay(toDate(prev.createdAt), d) && !(state.unreadMarker === m.id);
    els.push(messageEl(m, grouped, ctx, lastMine && lastMine.id === m.id));
    prev = m;
  }
  mount(list, els);
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
async function msgAction(p, opts) { try { const nm = await api(p, opts); replaceMessage(nm); renderMessages(); return nm; } catch (e) { fail(e); return null; } }
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

// ---------- pin bar ----------
async function renderPinBar() {
  const bar = $("#pinBar");
  if (!bar || !state.current) return;
  const cid = state.current;
  let pins = [];
  try { pins = (await api(`api/conversations/${cid}/pins`)).messages; } catch (e) { return; }
  if (cid !== state.current) return;
  state.pins = pins;
  if (!pins.length) { clear(bar); bar.className = ""; return; }
  let i = 0;
  const text = h("span", { class: "pin-text ellipsis" });
  const paint = () => { const p = pins[i]; text.textContent = (p.kind === "poll" ? "📊 " + p.poll.question : p.kind === "card" ? (p.card ? (CARD_ICONS[p.card.type] || "📄") + " " + p.card.title : p.body) : plainText(p.body) || (p.attachments[0] ? "📎 " + p.attachments[0].name : "")) || "…"; };
  paint();
  bar.className = "pin-bar";
  mount(bar,
    h("button", { class: "pin-main", type: "button", title: "Go to the pinned message", onclick: () => { jumpTo(pins[i].id); if (pins.length > 1) { i = (i + 1) % pins.length; paint(); count.textContent = `${i + 1}/${pins.length}`; } } },
      h("span", null, "📌"), text),
    h("span", { class: "hint", id: "pinCount" }, pins.length > 1 ? `1/${pins.length}` : ""),
    h("button", { class: "icon-btn", type: "button", title: "All pinned messages", "aria-label": "All pinned messages", onclick: () => pinsDialog() }, "☰"));
  const count = $("#pinCount");
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
  const wrap = h("div", { class: "voice" + (a.voice && heard === false && a.uploadedBy !== state.me.id ? " unheard" : "") }, btn, bar, time, speed, audio,
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
