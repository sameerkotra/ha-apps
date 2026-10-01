/* The composer: text, @mentions, emoji, files and photos, voice messages, reply and edit. */
"use strict";

let lastTyping = 0;
let voiceHoldUp = false;
window.addEventListener("pointerup", (e) => {
  if (rec && rec.hold) { const cancel = rec.startX !== null && e.clientX < rec.startX - 80; rec.finish(!cancel); }
  else voiceHoldUp = true;
});
window.addEventListener("pointercancel", () => { if (rec && rec.hold) rec.finish(false); else voiceHoldUp = true; });

function composer(c) {
  const cid = c.id;
  const ta = h("textarea", { id: "composerText", rows: "1", placeholder: c.kind === "personal" ? "Write a note" : "Message", "aria-label": "Message", maxlength: "8000" });
  // a draft typed here, else the one saved from another device (§16.8)
  ta.value = state.drafts[cid] != null ? state.drafts[cid] : (c.draft && c.draft.body) || "";
  const fileIn = h("input", { type: "file", multiple: true, hidden: true, id: "fileIn" });
  const camIn = h("input", { type: "file", accept: "image/*", capture: "environment", hidden: true, id: "camIn" });
  fileIn.addEventListener("change", () => { addFiles([...fileIn.files]); fileIn.value = ""; });
  camIn.addEventListener("change", () => { addFiles([...camIn.files]); camIn.value = ""; });
  const sendBtn = h("button", { class: "icon-btn send", id: "sendBtn", type: "button", "aria-label": "Send", title: "Send (right-click or hold: send later)", onclick: () => { if (!sendHeld) send(); sendHeld = false; } }, "➤");
  let sendHeld = false, holdTimer = null;
  if (c.kind !== "personal") {
    sendBtn.addEventListener("contextmenu", (e) => { e.preventDefault(); if (!state.editing) scheduleDialog(); });
    sendBtn.addEventListener("touchstart", () => { holdTimer = setTimeout(() => { sendHeld = true; if (!state.editing) scheduleDialog(); }, 600); }, { passive: true });
    ["touchend", "touchcancel", "touchmove"].forEach((ev) => sendBtn.addEventListener(ev, () => clearTimeout(holdTimer), { passive: true }));
  }
  const micBtn = h("button", { class: "icon-btn", id: "micBtn", type: "button", "aria-label": "Record a voice message", title: "Voice message", onclick: () => { if (FINE_POINTER) startVoice(); } }, "🎤");
  if (!FINE_POINTER) {
    // phones: hold to record, release to send, slide left to cancel (§15.6)
    micBtn.addEventListener("pointerdown", (e) => { e.preventDefault(); voiceHoldUp = false; startVoice({ x: e.clientX }); });
    micBtn.addEventListener("contextmenu", (e) => e.preventDefault());
  }
  const attach = h("button", { class: "icon-btn", type: "button", "aria-label": "Add", title: "Add a file, photo or poll", onclick: (e) => menu(e.currentTarget, [
    { label: "📎 File or document", run: () => fileIn.click() },
    { label: "📷 Photo or camera", run: () => camIn.click() },
    c.kind !== "personal" ? { label: "📊 Poll", run: () => pollDialog() } : null,
    !isChild() ? { label: "⏱ Disappear after…", run: () => perMessageDisappearMenu(attach) } : null,
    state.detail && state.detail.canAnnounce ? { label: state.announce[cid] ? "📢 Don't send as announcement" : "📢 Send as announcement", run: () => { state.announce[cid] = !state.announce[cid]; renderBanner(); } } : null,
    c.kind !== "personal" ? { label: "🕓 Send later…", run: () => scheduleDialog() } : null,
    { label: "Aa Formatting", run: () => openModal("Formatting", formattingHelp()) },
  ]) }, "＋");
  const emojiBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Emoji", title: "Emoji", onclick: (e) => emojiPicker(e.currentTarget, (em) => insertAtCaret(ta, em)) }, "😊");
  const row = h("div", { class: "composer-row", id: "composerRow" }, attach, ta, emojiBtn, micBtn, sendBtn, fileIn, camIn);
  const el = h("div", { class: "composer", id: "composer" },
    h("div", { id: "mentionBox", class: "mention-box", hidden: true, role: "listbox" }),
    h("div", { id: "composerBanner" }),
    h("div", { id: "pendingList", class: "pending-list" }),
    row);
  ta.addEventListener("input", () => { autosize(ta); updateSendButtons(); onMentionInput(ta); typingPing(c); if (!state.editing) saveServerDraft(cid, ta.value); });
  ta.addEventListener("keydown", (e) => {
    if (mentionKey(e, ta)) return;
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && FINE_POINTER) { e.preventDefault(); send(); }
    else if (e.key === "Escape" && (state.replyTo[cid] || state.editing)) { e.preventDefault(); cancelReplyEdit(); }
    else if (e.key === "ArrowUp" && !ta.value && FINE_POINTER) {
      const mine = [...state.msgs].reverse().find((m) => m.userId === state.me.id && canEdit(m));
      if (mine) { e.preventDefault(); startEdit(mine); }
    }
  });
  ta.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData ? e.clipboardData.files : [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop"); });
  el.addEventListener("dragleave", () => el.classList.remove("drop"));
  el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("drop"); addFiles([...e.dataTransfer.files]); });
  setTimeout(() => { autosize(ta); renderBanner(); renderPending(); updateSendButtons(); if (FINE_POINTER) ta.focus(); }, 0);
  return el;
}
function autosize(ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; }
function insertAtCaret(ta, text) {
  const s = ta.selectionStart ?? ta.value.length, e = ta.selectionEnd ?? ta.value.length;
  ta.value = ta.value.slice(0, s) + text + ta.value.slice(e);
  ta.selectionStart = ta.selectionEnd = s + text.length;
  ta.focus(); autosize(ta); updateSendButtons();
}
function saveDraft() {
  const ta = $("#composerText");
  if (ta && state.current && !state.editing) { state.drafts[state.current] = ta.value; flushServerDraft(state.current, ta.value); }
}
function typingPing(c) {
  if (c.kind === "personal" || !$("#composerText").value) return;
  if (Date.now() - lastTyping < 3000) return;
  lastTyping = Date.now();
  api(`api/conversations/${c.id}/typing`, { method: "POST" }).catch(() => {});
}
function pendingOf(cid) { return state.pending[cid] || (state.pending[cid] = []); }
function updateSendButtons() {
  const ta = $("#composerText");
  if (!ta) return;
  const has = ta.value.trim() || pendingOf(state.current).length || state.editing;
  $("#sendBtn").hidden = !has;
  $("#micBtn").hidden = !!has;
}

// ---------- reply / edit ----------
function startReply(m) {
  state.editing = null;
  state.replyTo[state.current] = m;
  renderBanner();
  const ta = $("#composerText"); if (ta) ta.focus();
}
function startEdit(m) {
  saveDraft();
  state.editing = m;
  delete state.replyTo[state.current];
  const ta = $("#composerText");
  if (!ta) return;
  ta.value = m.body; autosize(ta); ta.focus();
  renderBanner(); updateSendButtons();
}
function cancelReplyEdit() {
  const wasEditing = state.editing;
  state.editing = null;
  delete state.replyTo[state.current];
  if (wasEditing) { const ta = $("#composerText"); ta.value = state.drafts[state.current] || ""; autosize(ta); }
  renderBanner(); updateSendButtons();
}
function renderBanner() {
  const box = $("#composerBanner");
  if (!box) return;
  const r = state.replyTo[state.current];
  if (state.editing) {
    mount(box, h("div", { class: "banner" }, h("span", null, "✏ Editing"), h("span", { class: "grow ellipsis hint" }, state.editing.body),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Cancel editing", onclick: cancelReplyEdit }, "✕")));
  } else {
    const cid = state.current;
    const c = convById(cid) || state.detail || {};
    const exp = state.expiresIn[cid];
    mount(box,
      r ? h("div", { class: "banner" }, h("span", null, "↩ ", r.userId === state.me.id ? "You" : r.author),
        h("span", { class: "grow ellipsis hint" }, r.body || (r.poll ? "📊 " + r.poll.question : r.attachments.length ? "📎 " + r.attachments[0].name : "")),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Cancel reply", onclick: cancelReplyEdit }, "✕")) : null,
      state.announce[cid] ? h("div", { class: "banner ann" }, h("span", { class: "grow" }, "📢 Sending as an announcement — it reaches everyone, even if they muted this chat"),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Not an announcement", onclick: () => { state.announce[cid] = false; renderBanner(); } }, "✕")) : null,
      exp ? h("div", { class: "banner" }, h("span", { class: "grow" }, "⏱ This message disappears after " + durationLabel(exp)),
        h("button", { class: "icon-btn", type: "button", "aria-label": "Don't disappear", onclick: () => { delete state.expiresIn[cid]; renderBanner(); } }, "✕"))
        : c.disappearSeconds ? h("div", { class: "hint dis-note" }, "⏱ New messages disappear after " + durationLabel(c.disappearSeconds)) : null);
  }
}

// ---------- files ----------
function addFiles(list) {
  const cid = state.current;
  if (!cid || !list.length) return;
  const blocked = new Set((state.me.app.blockedExtensions || "").split(",").filter(Boolean));
  const max = state.me.app.maxUploadMb * 1024 * 1024;
  const pend = pendingOf(cid);
  for (const f of list) {
    const ext = (f.name.includes(".") ? f.name.split(".").pop() : "").toLowerCase();
    if (blocked.has(ext)) { toast(`“.${ext}” files can't be shared here.`, { error: true }); continue; }
    if (f.size > max) { toast(`${f.name} is bigger than the ${state.me.app.maxUploadMb} MB limit.`, { error: true }); continue; }
    if (pend.length >= 10) { toast("At most 10 files per message.", { error: true }); break; }
    const item = { key: Math.random().toString(36).slice(2), file: f, name: f.name || "file", progress: 0, id: null, error: null, image: /^image\/(jpeg|png|webp)$/.test(f.type) };
    pend.push(item);
    startUpload(cid, item);
  }
  renderPending(); updateSendButtons();
}
function startUpload(cid, item, extra = "") {
  const original = state.sendOriginal[cid] ? "&original=true" : "";
  const up = uploadXhr(`api/conversations/${cid}/uploads?name=${encodeURIComponent(item.name)}${original}${extra}`, item.file, (p) => { item.progress = p; paintPending(item); });
  item.xhr = up.xhr; item.error = null; item.id = null; item.progress = 0;
  item.promise = up.promise.then((r) => { item.id = r.id; item.info = r; item.xhr = null; paintPending(item); return r; })
    .catch((e) => { item.xhr = null; if (e.status !== -1) { item.error = e.message; paintPending(item); } throw e; });
  item.promise.catch(() => {});
}
function removePending(cid, item) {
  const pend = pendingOf(cid);
  const i = pend.indexOf(item);
  if (i >= 0) pend.splice(i, 1);
  if (item.xhr) item.xhr.abort();
  else if (item.id) api(`api/uploads/${item.id}`, { method: "DELETE" }).catch(() => {});
  renderPending(); updateSendButtons();
}
function paintPending(item) {
  const el = document.getElementById("pend-" + item.key);
  if (!el) return;
  const bar = el.querySelector(".pbar > span");
  if (bar) bar.style.width = Math.round((item.id ? 1 : item.progress) * 100) + "%";
  const st = el.querySelector(".pstate");
  if (st) st.textContent = item.error ? "⚠ " + item.error : item.id ? (item.info && item.info.originalSize ? `smaller: ${fmtSize(item.info.size)}` : fmtSize(item.file.size)) : Math.round(item.progress * 100) + "%";
  el.classList.toggle("err", !!item.error);
}
function renderPending() {
  const box = $("#pendingList");
  if (!box) return;
  const cid = state.current;
  const pend = pendingOf(cid);
  const hasImages = pend.some((p) => p.image);
  mount(box,
    pend.map((item) => {
      const el = h("div", { class: "pending", id: "pend-" + item.key },
        h("span", null, item.image ? "🖼" : "📎"), h("span", { class: "grow ellipsis" }, item.name),
        h("span", { class: "pstate hint" }),
        h("span", { class: "pbar" }, h("span")),
        item.error ? h("button", { class: "btn small", type: "button", onclick: () => { startUpload(cid, item); renderPending(); } }, "Retry") : null,
        h("button", { class: "icon-btn", type: "button", "aria-label": "Remove " + item.name, onclick: () => removePending(cid, item) }, "✕"));
      setTimeout(() => paintPending(item), 0);
      return el;
    }),
    hasImages ? h("label", { class: "check small" }, h("input", { type: "checkbox", checked: !!state.sendOriginal[cid], onchange: (e) => {
      state.sendOriginal[cid] = e.target.checked;
      for (const item of pend.filter((p) => p.image)) {
        if (item.xhr) item.xhr.abort(); else if (item.id) api(`api/uploads/${item.id}`, { method: "DELETE" }).catch(() => {});
        startUpload(cid, item);
      }
      renderPending();
    } }), h("span", null, "Send photos at original size", h("span", { class: "hint" }, " (keeps the photo's location, if it has one)"))) : null);
}

// ---------- sending ----------
async function send() {
  const cid = state.current;
  const ta = $("#composerText");
  if (!cid || !ta) return;
  const text = ta.value.replace(/\s+$/, "");
  if (state.editing) {
    const m = state.editing;
    if (text === m.body) { cancelReplyEdit(); return; }
    const nm = await msgAction(`api/messages/${m.id}`, { method: "PATCH", body: { body: text, mentions: mentionIdsIn(text) } });
    if (nm) cancelReplyEdit();
    return;
  }
  const pend = pendingOf(cid);
  if (pend.some((p) => !p.id && !p.error)) {
    toast("Waiting for files to finish uploading…");
    try { await Promise.all(pend.filter((p) => !p.id && !p.error).map((p) => p.promise)); } catch (e) { /* shown on the item */ }
    if (state.current !== cid) return;
  }
  if (pend.some((p) => p.error)) { toast("Remove or retry the files that didn't upload.", { error: true }); return; }
  if (!text.trim() && !pend.length) return;
  const reply = state.replyTo[cid];
  const body = { body: text, replyTo: reply ? reply.id : null, attachmentIds: pend.map((p) => p.id), mentions: mentionIdsIn(text),
    expiresIn: state.expiresIn[cid] ?? null, announcement: !!state.announce[cid] };
  const sendBtn = $("#sendBtn");
  sendBtn.disabled = true;
  cancelServerDraft(cid);            // a draft save still waiting must not bring the text back after sending
  try {
    const m = await api(`api/conversations/${cid}/messages`, { method: "POST", body });
    if (state.current === cid) {
      ta.value = ""; autosize(ta);
      state.drafts[cid] = "";
      state.pending[cid] = [];
      delete state.replyTo[cid];
      delete state.expiresIn[cid];
      delete state.announce[cid];
      cancelServerDraft(cid);
      const conv0 = convById(cid); if (conv0) conv0.draft = null;
      if (m.announcement && state.detail) { state.detail.announcements = [m, ...(state.detail.announcements || [])]; renderAnnouncements(); }
      renderBanner(); renderPending();
      if (!replaceMessage(m)) state.msgs.push(m);
      state.unreadMarker = null;
      if (state.moreAfter) { openChat(cid); return; }
      renderMessages(); scrollBottom();
      const c = convById(cid); if (c) { c.lastMessage = { id: m.id, preview: previewOf(m), at: m.createdAt, by: state.me.id }; c.lastActivityAt = m.createdAt; c.lastReadId = m.id; c.unread = 0; c.mentionUnread = 0; }
      sortConvs(); renderConvList();
    }
  } catch (e) { fail(e); } finally { sendBtn.disabled = false; updateSendButtons(); }
}
function sortConvs() {
  state.convs.sort((a, b) => (a.kind !== "personal") - (b.kind !== "personal") || (!a.pinned) - (!b.pinned) || String(b.lastActivityAt).localeCompare(String(a.lastActivityAt)));
}

// ---------- @mentions ----------
let mentionState = null;
function mentionCandidates(q) {
  const d = state.detail;
  if (!d || d.kind === "personal") return [];
  const list = d.members.filter((m) => m.id !== state.me.id && !m.disabled).map((m) => ({ id: m.id, name: m.name }));
  if (d.kind === "group") list.push({ id: "everyone", name: "everyone" });
  const ql = q.toLowerCase();
  return list.filter((m) => m.name.toLowerCase().startsWith(ql) || m.name.toLowerCase().split(/\s+/).some((w) => w.startsWith(ql))).slice(0, 8);
}
function onMentionInput(ta) {
  const box = $("#mentionBox");
  const before = ta.value.slice(0, ta.selectionStart);
  const m = /(^|\s)@([\p{L}\p{N}_]{0,20})$/u.exec(before);
  if (!m) { box.hidden = true; mentionState = null; return; }
  const cands = mentionCandidates(m[2]);
  if (!cands.length) { box.hidden = true; mentionState = null; return; }
  mentionState = { start: before.length - m[2].length - 1, cands, sel: 0 };
  paintMentions(ta);
}
function paintMentions(ta) {
  const box = $("#mentionBox");
  box.hidden = false;
  mount(box, mentionState.cands.map((c, i) => h("button", { type: "button", role: "option", class: i === mentionState.sel ? "sel" : "", onmousedown: (e) => { e.preventDefault(); pickMention(ta, i); } },
    c.id === "everyone" ? h("span", { class: "avatar small group" }, "@") : avatar(c.name, c.id, { small: true }), c.id === "everyone" ? "everyone — tell the whole group" : c.name)));
}
function mentionKey(e, ta) {
  if (!mentionState) return false;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = mentionState.cands.length; mentionState.sel = (mentionState.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; paintMentions(ta); return true; }
  if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pickMention(ta, mentionState.sel); return true; }
  if (e.key === "Escape") { e.preventDefault(); $("#mentionBox").hidden = true; mentionState = null; return true; }
  return false;
}
function pickMention(ta, i) {
  const c = mentionState.cands[i];
  const insert = "@" + c.name + " ";
  const end = ta.selectionStart;
  ta.value = ta.value.slice(0, mentionState.start) + insert + ta.value.slice(end);
  ta.selectionStart = ta.selectionEnd = mentionState.start + insert.length;
  $("#mentionBox").hidden = true; mentionState = null;
  ta.focus(); autosize(ta); updateSendButtons();
}
function mentionIdsIn(text) {
  const d = state.detail;
  if (!d || d.kind === "personal") return [];
  const low = text.toLowerCase();
  return d.members.filter((m) => m.id !== state.me.id && low.includes("@" + m.name.toLowerCase())).map((m) => m.id);
}

// ---------- emoji ----------
function emojiPicker(anchor, onPick, at) {
  const grid = h("div", { class: "emoji-grid" }, EMOJI.map((e) => h("button", { type: "button", class: "icon-btn", "aria-label": e, onclick: () => { closeMenus(); onPick(e); } }, e)));
  const pos = at && at.x !== undefined ? at : null;
  menu(anchor instanceof Element ? anchor : (at instanceof Element ? at : document.body), [{ row: grid }], pos);
}

// ---------- voice messages ----------
let rec = null;
function pickAudioMime() {
  const cands = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return cands.find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || "";
}
async function startVoice(hold) {
  if (!window.isSecureContext || !navigator.mediaDevices || !window.MediaRecorder) {
    openModal("Voice messages", h("p", null, "This browser can't record here. The microphone needs a secure (https) connection and permission — inside the Home Assistant app it may be blocked. Try Home Assistant in your phone's browser, or on a computer."));
    return;
  }
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); }
  catch (e) { toast("Microphone permission was refused.", { error: true }); return; }
  const mime = pickAudioMime();
  const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks = [];
  mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
  const started = Date.now();
  const max = state.me.app.voiceMaxSeconds;
  const timeEl = h("span", { class: "mono" }, "0:00");
  const level = h("span", { class: "level" }, h("span"));
  let ac = null, raf = null;
  try {
    ac = new (window.AudioContext || window.webkitAudioContext)();
    const an = ac.createAnalyser(); an.fftSize = 256;
    ac.createMediaStreamSource(stream).connect(an);
    const buf = new Uint8Array(an.frequencyBinCount);
    const tickLevel = () => { an.getByteTimeDomainData(buf); let peak = 0; for (const v of buf) peak = Math.max(peak, Math.abs(v - 128)); level.firstChild.style.width = Math.min(100, peak * 1.6) + "%"; raf = requestAnimationFrame(tickLevel); };
    tickLevel();
  } catch (e) { /* no meter */ }
  const cid = state.current;
  const stopAll = () => { clearInterval(timer); if (raf) cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); if (ac) ac.close().catch(() => {}); rec = null; };
  let finishing = false;
  const finish = (sendIt) => {
    if (!rec || finishing) return;
    finishing = true;
    clearInterval(timer);
    const secs = (Date.now() - started) / 1000;
    mr.onstop = async () => {
      stopAll();
      restoreRow();
      if (!sendIt || secs < 0.7) return;
      const type = (mr.mimeType || mime || "audio/webm").split(";")[0];
      const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
      const now = new Date();
      const name = `Voice ${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")} ${String(now.getHours()).padStart(2, "0")}.${String(now.getMinutes()).padStart(2, "0")}.${ext}`;
      const file = new File(chunks, name, { type });
      try {
        const up = uploadXhr(`api/conversations/${cid}/uploads?name=${encodeURIComponent(name)}&voice=true&duration=${Math.min(secs, max).toFixed(1)}`, file);
        toast("Sending voice message…");
        const r = await up.promise;
        const m = await api(`api/conversations/${cid}/messages`, { method: "POST", body: { body: "", attachmentIds: [r.id], replyTo: state.replyTo[cid] ? state.replyTo[cid].id : null } });
        delete state.replyTo[cid];
        if (state.current === cid) { if (!replaceMessage(m)) state.msgs.push(m); renderBanner(); renderMessages(); scrollBottom(); }
      } catch (e) { fail(e); }
    };
    mr.stop();
  };
  const timer = setInterval(() => {
    const s = (Date.now() - started) / 1000;
    timeEl.textContent = fmtDuration(s) + (s > max - 15 ? ` / ${fmtDuration(max)}` : "");
    if (s >= max) finish(true);
  }, 250);
  rec = { finish, hold: !!hold, startX: hold ? hold.x : null };
  const row = $("#composerRow");
  const saved = [...row.childNodes];
  const restoreRow = () => { if (row.isConnected) mount(row, saved); updateSendButtons(); };
  mount(row, h("div", { class: "recording" },
    h("button", { class: "icon-btn", type: "button", "aria-label": "Cancel recording", title: "Cancel", onclick: () => finish(false) }, "✕"),
    h("span", { class: "rec-dot" }), timeEl, level, h("span", { class: "grow hint" }, hold ? "Release to send · slide left to cancel" : "Recording…"),
    h("button", { class: "icon-btn send", type: "button", "aria-label": "Send voice message", title: "Send", onclick: () => finish(true) }, "➤")));
  mr.start(250);
  if (hold && voiceHoldUp) finish(false);      // released before the microphone was ready
}
