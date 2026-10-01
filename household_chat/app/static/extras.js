/* Disappearing messages, announcements, send later, drafts, downloading a chat,
   shared folders. */
"use strict";

const DURATIONS = [[3600, "1 hour"], [86400, "1 day"], [604800, "7 days"], [2592000, "30 days"]];
function durationLabel(s) { const d = DURATIONS.find(([v]) => v === s); return d ? d[1] : ""; }
function timeLeft(iso) {
  const ms = toDate(iso) - Date.now();
  if (ms <= 0) return "now";
  const m = Math.round(ms / 60000);
  if (m < 60) return m + " min";
  const hh = Math.round(m / 60);
  if (hh < 48) return hh + " h";
  return Math.round(hh / 24) + " d";
}
function isChild() { return !!(state.me && state.me.isChild); }

// ---------- disappearing messages ----------
function canSetDisappearing(c) {
  if (isChild() || c.readOnly) return false;
  if (c.kind === "direct") return true;
  return c.kind === "group" && ["owner", "admin"].includes(myRole());
}
function disappearDialog() {
  const c = convById(state.current) || state.detail;
  const set = async (v) => {
    try { await api(`api/conversations/${c.id}`, { method: "PATCH", body: { disappearSeconds: v } }); m.close(); await loadConvs(); openChat(c.id); }
    catch (e) { fail(e); }
  };
  const m = openModal("Disappearing messages", h("div", null,
    h("p", { class: "hint" }, "New messages in this chat are deleted completely that long after they're sent — for everyone, with their files. Messages already sent keep their own setting."),
    [[null, "Off"], ...DURATIONS].map(([v, l]) => h("label", { class: "check" },
      h("input", { type: "radio", name: "dis", checked: (c.disappearSeconds || null) === v, onchange: () => set(v) }), h("span", null, l))),
    h("p", { class: "hint" }, "Anyone in the chat can still copy or screenshot a message before it goes, and Home Assistant's own backups taken meanwhile keep it until they're deleted.")));
}
function perMessageDisappearMenu(anchor) {
  const cid = state.current;
  const c = convById(cid) || state.detail;
  const cur = state.expiresIn[cid];
  menu(anchor, [
    c.disappearSeconds ? null : { label: (cur == null ? "✓ " : "") + "Don't disappear", run: () => { delete state.expiresIn[cid]; renderBanner(); } },
    ...DURATIONS.map(([v, l]) => ({ label: (cur === v || (cur == null && c.disappearSeconds === v) ? "✓ " : "") + "Disappear after " + l, run: () => { state.expiresIn[cid] = v; renderBanner(); } })),
  ]);
}

// ---------- announcements ----------
function announcementBlock(m) {
  const a = m.announcement;
  const mine = m.userId === state.me.id;
  const pendingMe = a.pending.includes(state.me.id);
  const box = h("div", { class: "ann-block" },
    h("div", { class: "ann-title" }, "📢 Announcement", a.closed ? h("span", { class: "hint" }, " · done") : null));
  if (!a.closed && pendingMe) {
    box.appendChild(h("button", { class: "btn small primary", type: "button", onclick: () => msgAction(`api/messages/${m.id}/ack`, { method: "POST" }).then(renderAnnouncements) }, "👍 Got it"));
  }
  const seen = a.acks.map(nameOf), waiting = a.pending.map(nameOf);
  box.appendChild(h("div", { class: "hint" }, [seen.length ? "Got it: " + seen.join(", ") : null, waiting.length ? "Waiting for: " + waiting.join(", ") : null].filter(Boolean).join(" · ")));
  if (mine && !a.closed) {
    box.appendChild(h("div", { class: "row wrap" },
      !a.reminded ? h("button", { class: "btn small", type: "button", onclick: () => msgAction(`api/messages/${m.id}/announcement-reminder`, { method: "POST" }).then((r) => { if (r) toast("Reminder sent"); }) }, "Send a reminder") : h("span", { class: "hint" }, "Reminder sent"),
      h("button", { class: "btn small ghost", type: "button", onclick: () => msgAction(`api/messages/${m.id}/announcement-close`, { method: "POST" }).then(renderAnnouncements) }, "Take it down")));
  }
  return box;
}
function renderAnnouncements() {
  const bar = $("#annBar");
  if (!bar || !state.detail) return;
  const list = (state.detail.announcements || []).map((a) => state.msgs.find((x) => x.id === a.id) || a).filter((a) => a.announcement && !a.announcement.closed);
  if (!list.length) { clear(bar); bar.className = ""; return; }
  bar.className = "ann-bar";
  mount(bar, list.map((a) => h("div", { class: "ann-row" },
    h("button", { class: "pin-main", type: "button", onclick: () => jumpTo(a.id) }, h("span", null, "📢"), h("span", { class: "ellipsis grow" }, plainText(a.body) || "Announcement")),
    a.announcement.pending.includes(state.me.id) ? h("button", { class: "btn small primary", type: "button", onclick: async () => {
      const r = await msgAction(`api/messages/${a.id}/ack`, { method: "POST" });
      if (r) { const i = state.detail.announcements.findIndex((x) => x.id === r.id); if (i >= 0) state.detail.announcements[i] = r; renderAnnouncements(); }
    } }, "Got it") : h("span", { class: "hint" }, `${a.announcement.acks.length}/${a.announcement.acks.length + a.announcement.pending.length}`))));
}

// ---------- send later ----------
function localInputValue(d) { const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; }
function scheduleDialog() {
  const cid = state.current;
  const ta = $("#composerText");
  const text = ta ? ta.value.replace(/\s+$/, "") : "";
  const pend = pendingOf(cid);
  if (!text.trim() && !pend.length) { toast("Type the message first, then choose Send later.", { error: true }); return; }
  if (pend.some((p) => !p.id)) { toast("Wait for the files to finish uploading.", { error: true }); return; }
  const at = h("input", { type: "datetime-local", "aria-label": "Send at", value: localInputValue(new Date(Date.now() + 3600000)), min: localInputValue(new Date()) });
  const err = h("div", { class: "error" });
  const m = openModal("Send later", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const when = new Date(at.value);
    if (isNaN(when)) { err.textContent = "Choose a date and time."; return; }
    try {
      await api(`api/conversations/${cid}/scheduled`, { method: "POST", body: {
        sendAt: when.toISOString(), body: text, attachmentIds: pend.map((p) => p.id), mentions: mentionIdsIn(text),
        replyTo: state.replyTo[cid] ? state.replyTo[cid].id : null, expiresIn: state.expiresIn[cid] ?? null, announcement: !!state.announce[cid] } });
      m.close();
      cancelServerDraft(cid);
      ta.value = ""; autosize(ta); state.drafts[cid] = ""; state.pending[cid] = []; delete state.replyTo[cid]; delete state.announce[cid];
      renderBanner(); renderPending(); updateSendButtons();
      toast("🕓 Scheduled for " + fmtFull(when.toISOString()));
      renderScheduledBar();
    } catch (x) { err.textContent = x.message; }
  } }, h("p", { class: "hint" }, "It waits here, visible only to you, and is posted as you at that time. You can change or cancel it until then."),
  field("Send at", at), err, h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Schedule"))));
}
async function renderScheduledBar() {
  const bar = $("#schedBar");
  if (!bar || !state.current) return;
  const cid = state.current;
  let list = [];
  try { list = (await api(`api/me/scheduled?conversation=${cid}`)).scheduled; } catch (e) { return; }
  if (cid !== state.current) return;
  if (!list.length) { clear(bar); bar.className = ""; return; }
  bar.className = "sched-bar";
  mount(bar, h("button", { class: "pin-main", type: "button", onclick: () => scheduledDialog(cid) },
    h("span", null, "🕓"), h("span", { class: "grow ellipsis" }, `${list.length} scheduled — next ${fmtFull(list[0].sendAt)}`)));
}
async function scheduledDialog(cid) {
  const body = h("div");
  let m;
  const load = async () => {
    let list;
    try { list = (await api("api/me/scheduled" + (cid ? `?conversation=${cid}` : ""))).scheduled; } catch (e) { fail(e); return; }
    if (!list.length) { mount(body, h("p", { class: "hint" }, "Nothing waiting to be sent. In a chat, ＋ → 🕓 Send later.")); return; }
    mount(body, h("div", { class: "result-list" }, list.map((s) => {
      const at = h("input", { type: "datetime-local", value: localInputValue(new Date(s.sendAt)), "aria-label": "Send at" });
      const txt = s.poll ? null : h("textarea", { rows: "2", "aria-label": "Message", maxlength: "8000" });
      if (txt) txt.value = s.body;
      return h("div", { class: "result card-ish" },
        h("div", { class: "hint" }, `${s.conversationName}${s.announcement ? " · 📢 announcement" : ""}${s.expiresIn ? " · ⏱ " + durationLabel(s.expiresIn) : ""}`),
        s.poll ? h("div", null, "📊 " + s.poll.question) : txt,
        s.attachments.length ? h("div", { class: "hint" }, "📎 " + s.attachments.map((a) => a.name).join(", ")) : null,
        h("div", { class: "row wrap" }, at,
          h("button", { class: "btn small", type: "button", onclick: async () => {
            try { await api(`api/scheduled/${s.id}`, { method: "PATCH", body: Object.assign({ sendAt: new Date(at.value).toISOString() }, txt ? { body: txt.value } : {}) }); toast("Saved"); load(); } catch (e) { fail(e); }
          } }, "Save"),
          h("button", { class: "btn small primary", type: "button", onclick: async () => { try { await api(`api/scheduled/${s.id}/send-now`, { method: "POST" }); toast("Sent"); load(); renderScheduledBar(); } catch (e) { fail(e); } } }, "Send now"),
          h("button", { class: "btn small danger", type: "button", onclick: async () => { try { await api(`api/scheduled/${s.id}`, { method: "DELETE" }); load(); renderScheduledBar(); } catch (e) { fail(e); } } }, "Delete")));
    })));
  };
  m = openModal("Scheduled messages", body, { wide: true, noFocus: true, onClose: () => renderScheduledBar() });
  load();
}

// ---------- drafts kept across devices ----------
const draftTimers = {};
function saveServerDraft(cid, text) {
  clearTimeout(draftTimers[cid]);
  draftTimers[cid] = setTimeout(() => {
    delete draftTimers[cid];
    api(`api/conversations/${cid}/draft`, { method: "PUT", body: { body: text } }).catch(() => {});
    const c = convById(cid);
    if (c) c.draft = text.trim() ? { body: text } : null;
  }, 1500);
}
function cancelServerDraft(cid) { clearTimeout(draftTimers[cid]); delete draftTimers[cid]; }
function flushServerDraft(cid, text) {
  // leaving the chat: save now rather than in 1.5 s (§16.8)
  if (!(cid in draftTimers)) return;
  clearTimeout(draftTimers[cid]); delete draftTimers[cid];
  api(`api/conversations/${cid}/draft`, { method: "PUT", body: { body: text } }).catch(() => {});
  const c = convById(cid);
  if (c) c.draft = text.trim() ? { body: text } : null;
}

// ---------- download a chat ----------
function exportDialog() {
  const c = convById(state.current) || state.detail;
  const from = h("input", { type: "date", "aria-label": "From" });
  const to = h("input", { type: "date", "aria-label": "To" });
  const btn = h("button", { class: "btn primary", type: "submit" }, "⬇ Download");
  const m = openModal("Download chat", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    btn.disabled = true;
    try {
      const q = [from.value ? "from=" + from.value : "", to.value ? "to=" + to.value : ""].filter(Boolean).join("&");
      const res = await api(`api/conversations/${c.id}/export${q ? "?" + q : ""}`, { raw: true });
      const cd = res.headers.get("content-disposition") || "";
      const mm = /filename\*=UTF-8''([^;]+)/.exec(cd) || /filename="?([^";]+)"?/.exec(cd);
      saveBlob(await res.blob(), mm ? decodeURIComponent(mm[1]) : "chat.zip");
      m.close();
    } catch (x) { fail(x); } finally { btn.disabled = false; }
  } }, h("p", { class: "hint" }, `A zip with a readable page of every message and the files (up to ${state.me.app.exportMaxMb} MB of files). Disappearing messages are left out.`),
  h("div", { class: "form-row" }, field("From (optional)", from), field("To (optional)", to)),
  h("div", { class: "notice" }, "Anyone who gets the zip can read this chat — keep it safe."),
  h("div", { class: "actions" }, btn)));
}

// ---------- shared folders ----------
function foldersMenu(anchor) {
  const d = state.detail;
  if (!d.folders || !d.folders.length) return;
  if (d.folders.length === 1) { folderBrowser(d.folders[0]); return; }
  menu(anchor, d.folders.map((f) => ({ label: "📂 " + f.label, run: () => folderBrowser(f) })));
}
function folderBrowser(f) {
  let path = "", mode = "ro", query = "";
  const body = h("div");
  const up = h("input", { type: "file", multiple: true, hidden: true });
  const search = h("input", { type: "search", class: "folder-search", placeholder: "Search this shared folder", "aria-label": "Search this shared folder",
    autocomplete: "off", enterkeyhint: "search" });
  const progress = h("div", { class: "upload-queue", hidden: true, role: "status", "aria-live": "polite" });
  const fileUrl = (dir, e) => `api/folders/${f.id}/file?path=${encodeURIComponent((dir ? dir + "/" : "") + e.name)}`;
  const viewable = (e) => /^image\/(png|jpeg|gif|webp)$|^application\/pdf$|^text\/plain$/.test(e.mime || "");
  // one file row; `dir` is the folder it's in, `where` shows that folder (search results)
  const fileRow = (dir, e, images, where) => h("div", { class: "file-chip" },
    h("span", { class: "file-icon" }, fileIcon({ mime: e.mime || "", voice: false, image: /^image\//.test(e.mime || "") })),
    h("span", { class: "grow" },
      viewable(e) ? h("button", { class: "link-btn", type: "button", onclick: () => {
        const list = /^image\//.test(e.mime) ? images : [e];
        viewer(list.map((x) => ({ name: x.name, size: x.size, mime: x.mime, image: /^image\//.test(x.mime), url: fileUrl(x.folder !== undefined ? x.folder : dir, x) })), Math.max(0, list.indexOf(e)));
      } }, e.name) : h("span", null, e.name),
      h("span", { class: "hint block" }, (where ? "📁 " + (dir || f.label) + " · " : "") + fmtSize(e.size) + " · " + fmtWhen(e.modified))),
    h("a", { class: "icon-btn", href: fileUrl(dir, e) + "&download=1", download: e.name, "aria-label": "Download " + e.name }, "⬇"));
  const folderRow = (target, name, where) => h("button", { class: "file-chip folder-row", type: "button",
    onclick: () => { path = target; query = ""; search.value = ""; load(); } },
    h("span", { class: "file-icon" }, "📁"), h("span", { class: "grow" }, name, where ? h("span", { class: "hint block" }, "in " + (where || f.label)) : null));
  const toolbar = () => mode === "rw" ? h("div", { class: "row wrap" },
    h("button", { class: "btn small", type: "button", onclick: () => up.click() }, "⬆ Upload files"),
    h("button", { class: "btn small", type: "button", onclick: () => {
      const name = h("input", { type: "text", maxlength: "100", "aria-label": "Folder name" });
      const nm = openModal("New folder", h("form", { onsubmit: async (ev) => {
        ev.preventDefault();
        if (!name.value.trim()) return;
        try { await api(`api/folders/${f.id}/mkdir`, { method: "POST", body: { path, name: name.value } }); nm.close(); load(); } catch (e) { fail(e); }
      } }, field("Name", name), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Create"))));
    } }, "＋ New folder"),
    FINE_POINTER ? h("span", { class: "hint" }, "or drop files here") : null) : h("p", { class: "hint" }, "Read-only");
  const load = async () => {
    const q = query.trim();
    if (q) {
      let r;
      try { r = await api(`api/folders/${f.id}/search?q=${encodeURIComponent(q)}`); } catch (e) { fail(e); return; }
      if (query.trim() !== q) return;                        // typed on meanwhile
      const images = r.results.filter((e) => !e.dir && /^image\/(png|jpeg|gif|webp)$/.test(e.mime));
      mount(body,
        h("div", { class: "hint" }, r.results.length ? `${r.results.length}${r.truncated ? "+" : ""} found in ${f.label}` : `Nothing called “${q}” in ${f.label}.`),
        r.results.length ? h("div", { class: "file-rows" }, r.results.map((e) => e.dir
          ? folderRow((e.folder ? e.folder + "/" : "") + e.name, e.name, e.folder || f.label)
          : fileRow(e.folder, e, images, true))) : null,
        r.truncated ? h("p", { class: "hint" }, "Only the first results are shown — type more to narrow it down.") : null);
      return;
    }
    let r;
    try { r = await api(`api/folders/${f.id}/list?path=${encodeURIComponent(path)}`); } catch (e) { fail(e); return; }
    mode = r.mode;
    const crumbs = [["", f.label], ...path.split("/").filter(Boolean).map((p, i, arr) => [arr.slice(0, i + 1).join("/"), p])];
    const images = r.entries.filter((e) => !e.dir && /^image\/(png|jpeg|gif|webp)$/.test(e.mime));
    mount(body,
      h("div", { class: "crumbs" }, crumbs.map(([p, l], i) => [i ? h("span", { class: "hint" }, " / ") : null,
        h("button", { class: "link-btn", type: "button", onclick: () => { path = p; load(); } }, l)])),
      toolbar(),
      r.entries.length ? h("div", { class: "file-rows" }, r.entries.map((e) => e.dir
        ? folderRow((path ? path + "/" : "") + e.name, e.name)
        : fileRow(path, e, images, false))) : h("p", { class: "hint" }, "Empty folder."));
  };
  const typed = debounce(() => load(), 250);
  search.addEventListener("input", () => { query = search.value; typed(); });
  search.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });

  // several files at once: one after another into the folder that's open, with progress and a summary
  let busy = false;
  const uploadAll = async (list) => {
    if (!list.length) return;
    if (busy) { toast("Wait for the current uploads to finish.", { error: true }); return; }
    if (mode !== "rw") { toast("This folder is read-only.", { error: true }); return; }
    busy = true;
    const target = path, failed = [];
    const bar = h("span", { class: "upload-bar-fill" });
    const line = h("span", { class: "grow ellipsis" });
    let current = null, cancelled = false;
    const cancel = h("button", { class: "btn small", type: "button", onclick: () => { cancelled = true; if (current) current.xhr.abort(); } }, "Cancel");
    mount(progress, h("div", { class: "row" }, line, cancel), h("span", { class: "upload-bar" }, bar));
    progress.hidden = false;
    let done = 0;
    for (const [i, file] of list.entries()) {
      if (cancelled) break;
      line.textContent = `Uploading ${i + 1} of ${list.length}: ${file.name}`;
      bar.style.width = "0%";
      current = uploadXhr(`api/folders/${f.id}/upload?path=${encodeURIComponent(target)}&name=${encodeURIComponent(file.name)}`, file,
        (p) => { bar.style.width = Math.round(p * 100) + "%"; });
      try { await current.promise; done += 1; }
      catch (e) { if (e.status !== -1) failed.push(`${file.name}: ${e.message}`); }
    }
    current = null;
    busy = false;
    progress.hidden = true;
    clear(progress);
    up.value = "";
    if (failed.length) {
      openModal("Some files weren't uploaded", h("div", null, h("p", null, `${done} of ${list.length} uploaded.`),
        h("ul", null, failed.map((x) => h("li", null, x)))));
    } else if (done) toast(done === 1 ? "Uploaded" : `Uploaded ${done} files`);
    if (!query.trim() && path === target) load();
  };
  up.addEventListener("change", () => uploadAll([...up.files]));
  const box = h("div", { class: "folder-browser" }, search, progress, up, body);
  box.addEventListener("dragover", (e) => { if (mode === "rw" && e.dataTransfer && [...e.dataTransfer.types].includes("Files")) { e.preventDefault(); box.classList.add("drop"); } });
  box.addEventListener("dragleave", (e) => { if (e.target === box) box.classList.remove("drop"); });
  box.addEventListener("drop", (e) => {
    box.classList.remove("drop");
    if (mode !== "rw" || !e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault(); e.stopPropagation();
    uploadAll([...e.dataTransfer.files]);
  });
  openModal("📂 " + f.label, box, { wide: true, noFocus: true });
  load();
}
