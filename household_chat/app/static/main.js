/* Start-up and live updates (Server-Sent Events, with polling as the fallback). */
"use strict";

async function boot() {
  try { state.me = await api("api/me"); }
  catch (e) { mount($("#app"), h("div", { class: "center" }, h("div", { class: "card narrow" }, h("h1", null, "Household Chat"), h("p", { class: "error" }, e.message)))); return; }
  renderSetupBanner();
  if (state.me.disabled) { renderDisabled(); return; }
  await Promise.all([loadPeople(), loadConvs().catch(fail)]);
  renderShell();
  const last = lsGet("hchat.lastChat");
  if (FINE_POINTER && last && convById(last)) openChat(last);
  else renderEmptyMain();
  connectLive();
  checkCurrentCall();            // a call ringing for me when the page opens (e.g. Answer on the phone)
  setInterval(() => { sendPresence(); }, 25000);
  setInterval(() => { renderTyping(); if (Object.keys(state.typing).length) renderConvList(); expireTyping(); }, 2000);
  setInterval(() => { refreshSubline(); if (state.page === "chat" && state.msgs.some((m) => m.expiresAt)) renderMessages(); }, 60000);
  document.addEventListener("visibilitychange", () => { sendPresence(); if (document.visibilityState === "visible") { markReadSoon(); if (!state.sse) catchUp(); } });
  window.addEventListener("focus", () => { sendPresence(); markReadSoon(); });
  window.addEventListener("blur", () => sendPresence());
  window.addEventListener("beforeunload", saveDraft);
}
function expireTyping() {
  const now = Date.now();
  for (const [cid, t] of Object.entries(state.typing)) {
    for (const [uid, x] of Object.entries(t)) if (x.until < now) delete t[uid];
    if (!Object.keys(t).length) delete state.typing[cid];
  }
}
const rememberChat = debounce(() => { if (state.current) lsSet("hchat.lastChat", state.current); }, 500);

// ---------- live ----------
function connectLive() {
  if (!window.EventSource) { startPolling(); return; }
  const es = new EventSource("api/stream");
  state.sse = es;
  const dot = () => { const d = $("#connDot"); if (d) d.className = "conn-dot" + (state.sse && state.sse.readyState === 1 ? " on" : state.polling ? " poll" : ""); };
  es.addEventListener("open", () => {
    if (state.sseSeen) catchUp();          // a new connection has no Last-Event-ID: fetch what was missed
    state.sseSeen = true; state.sseFails = 0; stopPolling(); dot();
  });
  es.addEventListener("hello", () => { dot(); checkCurrentCall(); });
  es.addEventListener("call", (e) => onCallEvent(JSON.parse(e.data)));
  es.addEventListener("error", () => {
    dot();
    if (es.readyState === 2) {
      state.sse = null;
      state.sseFails += 1;
      if (state.sseFails >= 3) { startPolling(); setTimeout(connectLive, 60000); }
      else setTimeout(connectLive, 3000 * state.sseFails);
    }
  });
  es.addEventListener("message", (e) => onMessageEvent(JSON.parse(e.data)));
  es.addEventListener("message_updated", (e) => onMessageUpdated(JSON.parse(e.data)));
  es.addEventListener("message_removed", (e) => {
    const d = JSON.parse(e.data);
    if (d.conversationId === state.current) {
      const gone = new Set(d.messageIds);
      state.msgs = state.msgs.filter((m) => !gone.has(m.id));
      state.msgs.forEach((m) => { if (m.replyTo && gone.has(m.replyTo.id)) m.replyTo = { gone: true }; });
      if (state.detail && state.detail.announcements) state.detail.announcements = state.detail.announcements.filter((a) => !gone.has(a.id));
      renderMessages(); renderPinBar(); renderAnnouncements();
    }
    refreshConvsSoon();
  });
  es.addEventListener("scheduled", (e) => {
    const d = JSON.parse(e.data);
    if (d.cancelled) toast(`Your scheduled message to ${d.conversationName} wasn't sent: ${d.reason}`, { error: true, ms: 9000 });
    renderScheduledBar();
  });
  es.addEventListener("read", (e) => {
    const d = JSON.parse(e.data);
    if (d.conversationId === state.current) {
      const r = state.reads.find((x) => x.userId === d.userId);
      if (r) r.lastReadId = d.lastReadId; else state.reads.push({ userId: d.userId, lastReadId: d.lastReadId });
      const mine = [...state.msgs].reverse().find((m) => m.userId === state.me.id && !m.deleted && !isNote(m));
      if (mine) { const el = document.getElementById("m" + mine.id); if (el) { const s = el.querySelector(".seen"); const txt = seenBy(mine); if (s) s.textContent = txt || ""; else if (txt) el.querySelector(".msg-col").appendChild(h("div", { class: "seen" }, txt)); } }
    }
    if (d.userId === state.me.id) refreshConvsSoon();
  });
  es.addEventListener("typing", (e) => {
    const d = JSON.parse(e.data);
    (state.typing[d.conversationId] = state.typing[d.conversationId] || {})[d.userId] = { name: d.name, until: Date.now() + 6000 };
    renderTyping(); renderConvList();
  });
  es.addEventListener("conversation", (e) => {
    const d = JSON.parse(e.data);
    refreshConvsSoon();
    if (d.id === state.current) {
      if (d.removed) { state.current = null; toast("You're no longer in that chat."); if (state.page === "chat") renderEmptyMain(); }
      else api(`api/conversations/${d.id}`).then((x) => { if (state.current === d.id) { state.detail = x; refreshSubline(); } }).catch(() => {});
    }
  });
  es.addEventListener("unread", () => refreshConvsSoon());
  es.addEventListener("presence", () => { refreshPresenceSoon(); });
  es.addEventListener("reminder", (e) => {
    const d = JSON.parse(e.data);
    toast("⏰ Reminder — tap to see the message", { ms: 8000, onclick: () => openChatAt(d.conversationId, d.messageId) });
  });
  es.addEventListener("settings", async () => { try { state.me = await api("api/me"); renderFilesBanner(); } catch (x) { /* ignore */ } });
  es.addEventListener("storage", async () => { try { state.me = await api("api/me"); renderFilesBanner(); } catch (x) { /* ignore */ } });
  es.addEventListener("closed", () => { es.close(); state.sse = null; location.reload(); });
  es.addEventListener("reload", () => { refreshConvsSoon(); if (state.current) openChat(state.current); });
}
const refreshConvsSoon = debounce(() => loadConvs().catch(() => {}), 300);
const refreshPresenceSoon = debounce(async () => {
  await loadPeople();
  await loadConvs().catch(() => {});
  refreshSubline();
  if (state.current && state.page === "chat") renderMessages();
}, 800);

function onMessageEvent(m, fromPoll) {
  const c = convById(m.conversationId);
  if (state.typing[m.conversationId] && m.userId) delete state.typing[m.conversationId][m.userId];
  if (!c) { refreshConvsSoon(); return; }
  if (m.conversationId === state.current && state.page === "chat" && !state.moreAfter) {
    const wasBottom = atBottom();
    if (!replaceMessage(Object.assign(m, { _fromEvent: true }))) state.msgs.push(m);
    if (state.typing[m.conversationId] && m.userId) delete state.typing[m.conversationId][m.userId];
    renderMessages(); renderTyping();
    if (wasBottom || m.userId === state.me.id) scrollBottom(true);
    else if (m.userId !== state.me.id) { state.newBelow += 1; updateToBottom(); }
    markReadSoon();
    if (m.pinned || m.kind === "system") renderPinBar();
    if (m.announcement && state.detail && !(state.detail.announcements || []).some((a) => a.id === m.id)) {
      state.detail.announcements = [m, ...(state.detail.announcements || [])]; renderAnnouncements();
    }
    if (m.userId === state.me.id) renderScheduledBar();
  }
  c.lastMessage = { id: m.id, preview: previewOf(m), at: m.createdAt, by: m.userId, author: m.author };
  c.lastActivityAt = m.createdAt;
  const looking = m.conversationId === state.current && state.page === "chat" && document.visibilityState === "visible" && atBottom();
  if (m.userId === state.me.id) { c.unread = 0; c.mentionUnread = 0; c.lastReadId = m.id; }
  else if (m.userId && !looking && c.kind !== "personal" && !fromPoll && (m.kind !== "call" || callMissedByMe(m))) {
    c.unread = (c.unread || 0) + 1;
    if (m.mentionAll || m.mentions.includes(state.me.id)) c.mentionUnread = (c.mentionUnread || 0) + 1;
  }
  if (state.typing[m.conversationId] && m.userId) delete state.typing[m.conversationId][m.userId];
  sortConvs(); renderConvList();
  rememberChat();
}
function onMessageUpdated(m) {
  if (m.conversationId === state.current && replaceMessage(Object.assign(m, { _fromEvent: true }))) { renderMessages(); renderPinBar(); }
  if (m.conversationId === state.current && m.announcement && state.detail && state.detail.announcements) {
    const i = state.detail.announcements.findIndex((a) => a.id === m.id);
    if (i >= 0) { state.detail.announcements[i] = m; renderAnnouncements(); }
  }
  const c = convById(m.conversationId);
  if (c && c.lastMessage && c.lastMessage.id === m.id) { c.lastMessage.preview = previewOf(m); renderConvList(); }
}
function previewOf(m) {
  if (m.kind === "system") return m.body;
  if (m.deleted) return "Message deleted";
  if (m.kind === "call") return callNoteText(m);
  if (m.kind === "poll") return "📊 " + m.poll.question;
  if (m.body) return plainText(m.body).slice(0, 100);
  const a = m.attachments[0];
  return a ? (a.voice ? "🎤 Voice message" : "📎 " + a.name) : "";
}

// ---------- polling fallback ----------
function startPolling() {
  if (state.polling) return;
  const tick = async () => {
    await catchUp();
    if (!call) checkCurrentCall();          // during a call, calls.js asks every second
    state.polling = setTimeout(tick, document.visibilityState === "visible" ? 5000 : 60000);
  };
  state.polling = setTimeout(tick, 1000);
  const d = $("#connDot"); if (d) d.className = "conn-dot poll";
}
function stopPolling() { if (state.polling) { clearTimeout(state.polling); state.polling = null; } }
async function catchUp() {
  try {
    await loadConvs();
    if (state.current && state.page === "chat" && state.msgs.length && !state.moreAfter) {
      const r = await api(`api/conversations/${state.current}/messages?after=${state.msgs[state.msgs.length - 1].id}`);
      if (r.messages.length) { r.messages.forEach((m) => onMessageEvent(m, true)); }
    }
  } catch (e) { /* offline */ }
}

const _openChat = openChat;
// remember the open chat for the next visit (desktop)
openChat = async function (cid, opts) { await _openChat(cid, opts); rememberChat(); };   // eslint-disable-line no-func-assign

boot();
