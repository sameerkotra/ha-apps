/* Dialogs: new chats, chat info and members, notifications, pins, files, search in chat, forward,
   reminders, polls and the viewer. */
"use strict";

// ---------- new chats ----------
async function newDirectDialog() {
  await loadPeople();
  const others = state.people.filter((p) => !p.you);
  const body = h("div", { class: "people-pick" }, others.length ? others.map((p) => h("button", { class: "person-row", type: "button", onclick: async () => {
    m.close();
    try { const r = await api("api/conversations/direct", { method: "POST", body: { userId: p.id } }); await loadConvs(); openChat(r.id); } catch (e) { fail(e); }
  } }, avatar(p.name, p.id), h("span", { class: "grow" }, h("span", { class: "block" }, p.name), h("span", { class: "hint block" }, presenceLine(p.id)))))
    : h("p", { class: "hint" }, "Nobody else has access yet — an admin enables people in Admin → People."));
  const m = openModal("Direct message", body);
}
// a call to people you pick (SPEC §15.12): one person → their direct chat; more → a group with exactly them
async function newCallDialog() {
  await loadPeople();
  const picks = new Set();
  const err = h("div", { class: "error" });
  const others = state.people.filter((p) => !p.you);
  const list = h("div", { class: "people-pick" }, others.map((p) => h("label", { class: "person-row" },
    h("input", { type: "checkbox", onchange: (e) => {
      if (e.target.checked && picks.size >= 3) { e.target.checked = false; err.textContent = "A call is at most four people: you and three others."; return; }
      err.textContent = "";
      if (e.target.checked) picks.add(p.id); else picks.delete(p.id);
    } }), avatar(p.name, p.id, { small: true }), h("span", null, p.name))));
  const go = async (kind) => {
    if (!picks.size) { err.textContent = "Pick at least one person."; return; }
    try {
      const r = await api("api/calls/chat", { method: "POST", body: { userIds: [...picks] } });
      m.close();
      await loadConvs();
      await openChat(r.id);
      const c = convById(r.id);
      if (c) startCall(c, kind);
    } catch (e) { err.textContent = e.message; }
  };
  const m = openModal("New call", h("div", null,
    others.length ? list : h("p", { class: "hint" }, "Nobody else has access yet — an admin enables people in Admin → People."),
    h("p", { class: "hint" }, "Up to three others. One person: your direct chat. More: a group with exactly these people (made for you if there isn't one), where the call's note goes."),
    err,
    h("div", { class: "row" }, h("span", { class: "grow" }),
      h("button", { class: "btn", type: "button", onclick: () => m.close() }, "Cancel"),
      h("button", { class: "btn", type: "button", onclick: () => go("video") }, "📹 Video call"),
      h("button", { class: "btn primary", type: "button", onclick: () => go("audio") }, "📞 Call"))));
}
async function newGroupDialog() {
  await loadPeople();
  const name = h("input", { type: "text", maxlength: "60", "aria-label": "Group name", placeholder: "e.g. Family, School runs" });
  const icon = h("input", { type: "text", maxlength: "8", "aria-label": "Icon (one emoji)", placeholder: "👪", class: "icon-input" });
  const desc = h("textarea", { maxlength: "500", "aria-label": "Description", placeholder: "Optional — what this group is for", rows: "2" });
  const picks = new Set();
  const list = h("div", { class: "people-pick" }, state.people.filter((p) => !p.you).map((p) => h("label", { class: "person-row" },
    h("input", { type: "checkbox", onchange: (e) => { if (e.target.checked) picks.add(p.id); else picks.delete(p.id); } }), avatar(p.name, p.id, { small: true }), h("span", null, p.name))));
  const err = h("div", { class: "error" });
  const m = openModal("New group", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try {
      const r = await api("api/conversations", { method: "POST", body: { name: name.value, icon: icon.value.trim() || null, description: desc.value || null, memberIds: [...picks] } });
      m.close(); await loadConvs(); openChat(r.id);
    } catch (x) { err.textContent = x.message; }
  } }, h("div", { class: "form-row" }, field("Name", name), field("Icon", icon)), field("Description", desc), h("div", { class: "lbl-sm" }, "People"), list, err,
    h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Create group"))));
}

// ---------- chat info ----------
async function chatInfoDialog() {
  const cid = state.current;
  let d;
  try { d = await api(`api/conversations/${cid}`); } catch (e) { fail(e); return; }
  state.detail = d;
  const me = d.members.find((x) => x.id === state.me.id) || {};
  const manager = d.kind === "group" && ["owner", "admin"].includes(me.role);
  const body = h("div", { class: "info" });
  let m;
  const redraw = async () => {
    try {
      state.detail = d = await api(`api/conversations/${cid}`); await loadConvs(); draw(); refreshSubline();
      const c = convById(cid); const old = $(".chat-head .avatar");
      if (state.current === cid && old && c) old.replaceWith(convIcon(c));
    } catch (e) { fail(e); }
  };
  const patch = async (b) => { try { d = await api(`api/conversations/${cid}`, { method: "PATCH", body: b }); state.detail = d; await loadConvs(); draw(); refreshSubline(); toast("Saved"); } catch (e) { fail(e); } };
  const draw = () => {
    const me2 = d.members.find((x) => x.id === state.me.id) || {};
    const mgr = d.kind === "group" && ["owner", "admin"].includes(me2.role);
    const descBox = h("div", { class: "desc" }, d.description ? renderText(d.description, { names: memberNames() }) : h("span", { class: "hint" }, d.kind === "personal" ? "Add a description for yourself." : "No description."));
    const canDesc = d.kind === "personal" || (d.kind === "direct" && !d.readOnly) || mgr;
    const parts = [
      h("div", { class: "info-head" },
        convIcon(d, { big: true }),
        h("div", { class: "grow" }, h("div", { class: "info-name" }, d.name),
          h("div", { class: "hint" }, d.kind === "personal" ? "Only you can see this in the app." : d.kind === "direct" ? presenceLine(d.otherUserId) : `Group · created ${fmtFull(d.createdAt)}`))),
      h("p", { class: "hint privacy-line" }, d.kind === "personal" ? "🔓 Not encrypted — whoever can get into the Home Assistant machine or its backups can read it. " : "🔓 Only its members can read it in the app, but it isn't encrypted — whoever can get into the Home Assistant machine or its backups can read it. ",
        h("button", { class: "link-btn", type: "button", onclick: () => { if (m) m.close(); showPage("settings"); } }, "More")),
      h("div", { class: "section" }, h("div", { class: "row" }, h("span", { class: "lbl-sm grow" }, "Description"),
        canDesc ? h("button", { class: "btn small", type: "button", onclick: () => {
          const ta = h("textarea", { maxlength: "500", rows: "4", "aria-label": "Description" }); ta.value = d.description || "";
          const dm = openModal("Description", h("form", { onsubmit: async (e) => { e.preventDefault(); await patch({ description: ta.value }); dm.close(); } },
            ta, h("p", { class: "hint" }, "Up to 500 characters; formatting works (Aa)."), h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Save"))));
        } }, "Edit") : null), descBox),
    ];
    if (d.kind === "group") {
      if (mgr) {
        const nm = h("input", { type: "text", maxlength: "60", value: d.groupName || d.name, "aria-label": "Group name" });
        const ic = h("input", { type: "text", maxlength: "8", value: d.icon || "", "aria-label": "Icon", class: "icon-input" });
        parts.push(h("form", { class: "section", onsubmit: (e) => { e.preventDefault(); patch({ name: nm.value, icon: ic.value.trim() || null }); } },
          h("div", { class: "form-row" }, field("Name", nm), field("Icon", ic)), h("button", { class: "btn small", type: "submit" }, "Save name and icon")));
        parts.push(h("label", { class: "check" }, h("input", { type: "checkbox", checked: d.newMembersSeeHistory, onchange: (e) => patch({ newMembersSeeHistory: e.target.checked }) }),
          h("span", null, "New members see earlier messages")));
      }
      parts.push(h("div", { class: "section" }, h("div", { class: "row" }, h("span", { class: "lbl-sm grow" }, `${d.members.length} members`),
        mgr ? h("button", { class: "btn small", type: "button", onclick: () => addMembersDialog(d, redraw) }, "＋ Add people") : null),
        d.members.map((x) => {
          const roleLbl = { owner: "Owner", admin: "Group admin", member: "" }[x.role];
          const acts = [];
          if (x.id !== state.me.id && !x.disabled) {
            if (mgr && x.role !== "owner" && !x.isChild) acts.push({ label: x.role === "admin" ? "Remove group admin" : "Make group admin", run: async () => { try { d = await api(`api/conversations/${cid}/members/${encodeURIComponent(x.id)}`, { method: "PATCH", body: { role: x.role === "admin" ? "member" : "admin" } }); draw(); } catch (e) { fail(e); } } });
            if (me2.role === "owner") acts.push({ label: "Make owner (hand over)", run: async () => { if (!await confirmDialog("Hand over", `Make ${x.name} the owner? You become a group admin.`, "Hand over")) return; try { d = await api(`api/conversations/${cid}/transfer`, { method: "POST", body: { userId: x.id } }); draw(); } catch (e) { fail(e); } } });
            if (me2.role === "owner" || (me2.role === "admin" && x.role === "member")) acts.push({ label: "Remove from group", danger: true, run: async () => { if (!await confirmDialog("Remove", `Remove ${x.name} from ${d.name}?`, "Remove", true)) return; try { d = await api(`api/conversations/${cid}/members/${encodeURIComponent(x.id)}`, { method: "DELETE" }); draw(); } catch (e) { fail(e); } } });
            acts.push({ label: "💬 Message " + x.name, run: async () => { try { const r = await api("api/conversations/direct", { method: "POST", body: { userId: x.id } }); m.close(); await loadConvs(); openChat(r.id); } catch (e) { fail(e); } } });
          }
          return h("div", { class: "person-row" }, avatar(x.name, x.id, { small: true }),
            h("span", { class: "grow" }, h("span", { class: "block" }, x.id === state.me.id ? x.name + " (you)" : x.name), h("span", { class: "hint block" }, presenceLine(x.id))),
            roleLbl ? h("span", { class: "chip" }, roleLbl) : null,
            acts.length ? h("button", { class: "icon-btn", type: "button", "aria-label": "Actions for " + x.name, onclick: (e) => menu(e.currentTarget, acts) }, "⋯") : null);
        })));
      parts.push(h("div", { class: "actions" },
        h("button", { class: "btn danger", type: "button", onclick: () => { m.close(); leaveGroup(d); } }, "Leave group"),
        me2.role === "owner" ? h("button", { class: "btn danger", type: "button", onclick: () => { m.close(); deleteGroupDialog(d); } }, "Delete group…") : null));
    }
    mount(body, parts);
  };
  draw();
  m = openModal(d.kind === "group" ? "Group info" : "Info", body, { noFocus: true });
}
async function addMembersDialog(d, after) {
  await loadPeople();
  const inIt = new Set(d.members.map((x) => x.id));
  const cands = state.people.filter((p) => !inIt.has(p.id));
  const picks = new Set();
  const m = openModal("Add people", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    if (!picks.size) return;
    try { await api(`api/conversations/${d.id}/members`, { method: "POST", body: { userIds: [...picks] } }); m.close(); after(); } catch (x) { fail(x); }
  } }, cands.length ? h("div", { class: "people-pick" }, cands.map((p) => h("label", { class: "person-row" },
    h("input", { type: "checkbox", onchange: (e) => { if (e.target.checked) picks.add(p.id); else picks.delete(p.id); } }), avatar(p.name, p.id, { small: true }), h("span", null, p.name))))
    : h("p", { class: "hint" }, "Everyone with access is already in this group."),
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit", disabled: !cands.length }, "Add"))));
}
async function leaveGroup(c) {
  if (!await confirmDialog("Leave group", `Leave ${c.name}? You won't see its messages or files any more.`, "Leave", true)) return;
  try { await api(`api/conversations/${c.id}/leave`, { method: "POST" }); state.current = null; await loadConvs(); renderEmptyMain(); } catch (e) { fail(e); }
}
function deleteGroupDialog(c) {
  const name = c.groupName || c.name;
  const input = h("input", { type: "text", "aria-label": "Group name" });
  const err = h("div", { class: "error" });
  const m = openModal("Delete group", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    try { await api(`api/conversations/${c.id}`, { method: "DELETE", body: { confirmName: input.value } }); m.close(); state.current = null; await loadConvs(); renderEmptyMain(); toast("Group deleted"); }
    catch (x) { err.textContent = x.message; }
  } }, h("div", { class: "notice danger" }, `This deletes ${name} for everyone: all its messages. Its files are moved to _deleted in /share and removed after 30 days.`),
  field(`Type “${name}” to confirm`, input), err, h("div", { class: "actions" }, h("button", { class: "btn danger", type: "submit" }, "Delete for everyone"))));
}

// ---------- notifications for one chat ----------
function chatNotifyDialog() {
  const c = convById(state.current);
  const opts = [["default", "Follow my settings", "Your level in Settings (" + ({ all: "all messages", direct_mentions: "direct messages and mentions", off: "off" }[state.me.settings.notifyLevel]) + ")"],
    ["all", "All messages", ""], ["mentions", "Mentions and replies only", ""], ["off", "Off", "Nothing from this chat"]];
  const setMute = async (hours) => {
    const until = hours === null ? null : hours === -1 ? new Date(Date.now() + 3650 * 86400000).toISOString() : new Date(Date.now() + hours * 3600000).toISOString();
    try { await api(`api/conversations/${c.id}/me`, { method: "PUT", body: { mutedUntil: until } }); await loadConvs(); m.close(); toast(until ? "Muted" : "Unmuted"); } catch (e) { fail(e); }
  };
  const muted = c.mutedUntil && new Date(c.mutedUntil) > new Date();
  const m = openModal("Notifications — " + c.name, h("div", null,
    opts.map(([v, label, hint]) => h("label", { class: "check" }, h("input", { type: "radio", name: "cn", checked: c.notify === v, onchange: async () => {
      try { await api(`api/conversations/${c.id}/me`, { method: "PUT", body: { notify: v } }); await loadConvs(); toast("Saved"); } catch (e) { fail(e); }
    } }), h("span", null, label, hint ? h("span", { class: "hint block" }, hint) : null))),
    h("div", { class: "lbl-sm" }, muted ? `Muted until ${fmtFull(c.mutedUntil)}` : "Mute"),
    h("div", { class: "row wrap" },
      muted ? h("button", { class: "btn", type: "button", onclick: () => setMute(null) }, "Unmute") : null,
      h("button", { class: "btn", type: "button", onclick: () => setMute(1) }, "1 hour"),
      h("button", { class: "btn", type: "button", onclick: () => setMute(8) }, "8 hours"),
      h("button", { class: "btn", type: "button", onclick: () => setMute(168) }, "1 week"),
      h("button", { class: "btn", type: "button", onclick: () => setMute(-1) }, "Until I turn it back on")),
    state.me.notifyLinked ? null : h("p", { class: "notice" }, "No phone is linked to you yet, so nothing is sent anyway — in Home Assistant: Settings → People → you → Track device.")));
}

// ---------- pins ----------
async function pinsDialog() {
  let pins;
  try { pins = (await api(`api/conversations/${state.current}/pins`)).messages; } catch (e) { fail(e); return; }
  const m = openModal("Pinned messages", pins.length ? h("div", { class: "result-list" }, pins.map((p) => h("button", { class: "result", type: "button", onclick: () => { m.close(); jumpTo(p.id); } },
    h("span", { class: "hint block" }, (p.userId === state.me.id ? "You" : p.author) + " · " + fmtFull(p.createdAt)),
    h("span", { class: "block ellipsis" }, p.kind === "poll" ? "📊 " + p.poll.question : p.body || (p.attachments[0] ? "📎 " + p.attachments[0].name : "")))))
    : h("p", { class: "hint" }, "Nothing pinned. Pin a message from its ⋯ menu (up to 10)."));
}

// ---------- files ----------
async function filesDialog(cid, title) {
  const body = h("div");
  let type = "";
  const load = async () => {
    let files;
    try { files = (await api(cid ? `api/conversations/${cid}/files?type=${type}` : `api/me/files?type=${type}`)).files; } catch (e) { fail(e); return; }
    mount(body, filesView(files, !cid, () => m.close()));
  };
  const tabs = h("div", { class: "tabs" }, [["", "All"], ["images", "Photos"], ["pdfs", "PDFs"], ["voice", "Voice"], ["other", "Other"]].map(([v, l]) =>
    h("button", { class: "tab" + (v === type ? " on" : ""), type: "button", onclick: (e) => { type = v; tabs.querySelectorAll(".tab").forEach((t) => t.classList.remove("on")); e.currentTarget.classList.add("on"); load(); } }, l)));
  const m = openModal("Files — " + title, h("div", null, tabs, body), { wide: true, noFocus: true });
  load();
}
function filesView(files, showChat, closeFn) {
  if (!files.length) return h("p", { class: "hint" }, "No files here yet.");
  const images = files.filter((f) => f.image && !f.missing);
  const others = files.filter((f) => !(f.image && !f.missing));
  return h("div", null,
    images.length ? h("div", { class: "file-grid" }, images.map((f, k) => h("button", { class: "img-btn", type: "button", title: `${f.name} · ${f.sender} · ${fmtFull(f.sentAt)}`, onclick: () => viewer(images, k) },
      h("img", { src: `api/files/${f.id}/thumb`, alt: f.name, loading: "lazy" })))) : null,
    others.length ? h("div", { class: "file-rows" }, others.map((f) => h("div", { class: "file-chip" },
      h("span", { class: "file-icon" }, fileIcon(f)),
      h("span", { class: "grow" },
        (f.mime === "application/pdf" || f.mime === "text/plain") && !f.missing ? h("button", { class: "link-btn", type: "button", onclick: () => viewer([f], 0) }, f.name) : h("span", null, f.name),
        h("span", { class: "hint block" }, [fmtSize(f.size), f.originalSize ? "made smaller from " + fmtSize(f.originalSize) : null, f.sender, showChat ? f.conversationName : null, fmtWhen(f.sentAt), f.missing ? "no longer available" : null].filter(Boolean).join(" · "))),
      h("button", { class: "icon-btn", type: "button", title: "Show in chat", "aria-label": "Show in chat", onclick: () => { if (closeFn) closeFn(); openChatAt(f.conversationId, f.messageId); } }, "💬"),
      f.missing ? null : h("a", { class: "icon-btn", href: `api/files/${f.id}?download=1`, download: f.name, "aria-label": "Download " + f.name }, "⬇")))) : null);
}
function openChatAt(cid, mid) {
  if (state.current === cid && state.page === "chat") jumpTo(mid);
  else openChat(cid, { around: mid });
}

// ---------- search in chat ----------
function searchInChatDialog() {
  const cid = state.current;
  const input = h("input", { type: "search", placeholder: "Search this chat", "aria-label": "Search this chat" });
  const out = h("div", { class: "result-list" });
  const run = debounce(async () => {
    const q = input.value.trim();
    if (!q) { clear(out); return; }
    try { const r = await api(`api/search?q=${encodeURIComponent(q)}&conversation=${cid}`); mount(out, searchResults(r, () => m.close())); } catch (e) { fail(e); }
  }, 250);
  input.addEventListener("input", run);
  const m = openModal("Search in chat", h("div", null, input, out), { wide: true });
}
function snippetEl(s) {
  const out = h("span", { class: "block snippet" });
  const parts = String(s).split(/(\u0002[^\u0003]*\u0003)/);
  for (const p of parts) {
    if (p.startsWith("\u0002")) out.appendChild(h("mark", null, p.slice(1, -1)));
    else if (p) out.appendChild(document.createTextNode(p));
  }
  return out;
}
function searchResults(r, closeFn) {
  if (!r.messages.length && !r.files.length) return h("p", { class: "hint" }, "Nothing found.");
  return h("div", null,
    r.messages.map((x) => h("button", { class: "result", type: "button", onclick: () => { if (closeFn) closeFn(); openChatAt(x.conversationId, x.id); } },
      h("span", { class: "hint block" }, `${x.conversationName} · ${x.author} · ${fmtFull(x.createdAt)}`), snippetEl(x.snippet))),
    r.files.length ? h("div", { class: "lbl-sm" }, "Files") : null,
    r.files.length ? filesView(r.files, true, closeFn) : null);
}

// ---------- forward ----------
function forwardDialog(ids) {
  const picks = new Set();
  const targets = state.convs.filter((c) => !c.readOnly);
  const m = openModal("Forward to…", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    if (!picks.size) { toast("Choose at least one chat.", { error: true }); return; }
    try { const r = await api("api/messages/forward", { method: "POST", body: { messageIds: ids, conversationIds: [...picks] } }); m.close(); toast(`Forwarded to ${picks.size} chat${picks.size === 1 ? "" : "s"}`); await loadConvs(); if (picks.has(state.current)) openChat(state.current); void r; }
    catch (x) { fail(x); }
  } }, h("div", { class: "people-pick" }, targets.map((c) => h("label", { class: "person-row" },
    h("input", { type: "checkbox", onchange: (e) => { if (e.target.checked) picks.add(c.id); else picks.delete(c.id); } }), convIcon(c, { small: true }), h("span", null, c.name)))),
  h("p", { class: "hint" }, "Files are copied into each chat. The forwarded message doesn't say where it came from."),
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Forward"))));
}

// ---------- remind me ----------
function remindDialog(msg) {
  const at = h("input", { type: "datetime-local", "aria-label": "Date and time" });
  const set = async (body) => { const r = await msgAction(`api/messages/${msg.id}/remind`, { method: "POST", body }); if (r) { m.close(); toast("⏰ Reminder set for " + fmtFull(r.reminder.dueAt)); } };
  const m = openModal("Remind me", h("div", null,
    h("div", { class: "row wrap" },
      h("button", { class: "btn", type: "button", onclick: () => set({ preset: "1h" }) }, "In 1 hour"),
      h("button", { class: "btn", type: "button", onclick: () => set({ preset: "evening" }) }, "This evening (18:00)"),
      h("button", { class: "btn", type: "button", onclick: () => set({ preset: "tomorrow" }) }, "Tomorrow 09:00"),
      h("button", { class: "btn", type: "button", onclick: () => set({ preset: "nextweek" }) }, "Next week")),
    h("form", { onsubmit: (e) => { e.preventDefault(); if (at.value) set({ at: at.value }); } },
      field("Or pick a date and time", at, `In Home Assistant's time zone (${state.me.timeZone}).`),
      h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Set reminder"))),
    h("p", { class: "hint" }, "Only you get the reminder, on your phone (if an admin linked one) and here.")), { noFocus: true });
}

// ---------- polls ----------
function pollDialog() {
  const cid = state.current;
  const q = h("input", { type: "text", maxlength: "200", "aria-label": "Question", placeholder: "Biryani or pizza tonight?" });
  const optsBox = h("div");
  const opts = [];
  const addOpt = (v = "") => {
    if (opts.length >= 10) return;
    const inp = h("input", { type: "text", maxlength: "100", "aria-label": `Option ${opts.length + 1}`, placeholder: `Option ${opts.length + 1}` });
    inp.value = v; opts.push(inp); optsBox.appendChild(inp);
  };
  addOpt(); addOpt();
  const multi = h("input", { type: "checkbox" });
  const closes = h("input", { type: "datetime-local", "aria-label": "Close on" });
  const err = h("div", { class: "error" });
  const m = openModal("New poll", h("form", { onsubmit: async (e) => {
    e.preventDefault();
    const options = opts.map((o) => o.value.trim()).filter(Boolean);
    try {
      const body = { question: q.value, options, multi: multi.checked, closesAt: closes.value ? new Date(closes.value).toISOString() : null };
      const r = await api(`api/conversations/${cid}/polls`, { method: "POST", body });
      m.close();
      if (state.current === cid) { if (!replaceMessage(r)) state.msgs.push(r); renderMessages(); scrollBottom(); }
    } catch (x) { err.textContent = x.message; }
  } }, field("Question", q), h("div", { class: "lbl-sm" }, "Options (2–10)"), optsBox,
  h("button", { class: "btn small", type: "button", onclick: () => addOpt() }, "＋ Add option"),
  h("label", { class: "check" }, multi, h("span", null, "Allow several answers")),
  field("Close on (optional)", closes), err,
  h("div", { class: "actions" }, h("button", { class: "btn primary", type: "submit" }, "Start poll"))));
}

// ---------- viewer ----------
function viewer(list, index) {
  let i = index;
  const stage = h("div", { class: "viewer-stage" });
  const caption = h("div", { class: "viewer-cap" });
  const prev = h("button", { class: "icon-btn nav prev", type: "button", "aria-label": "Previous", onclick: () => go(-1) }, "‹");
  const next = h("button", { class: "icon-btn nav next", type: "button", "aria-label": "Next", onclick: () => go(1) }, "›");
  const dl = h("a", { class: "btn small", download: "" }, "⬇ Download");
  const paint = async () => {
    const a = list[i];
    const src = a.url || `api/files/${a.id}`;
    dl.href = a.url ? a.url + "&download=1" : `api/files/${a.id}?download=1`; dl.setAttribute("download", a.name);
    caption.textContent = a.name + (a.size ? " · " + fmtSize(a.size) : "") + (list.length > 1 ? ` · ${i + 1} of ${list.length}` : "");
    prev.hidden = next.hidden = list.length < 2;
    if (a.image) mount(stage, h("img", { src, alt: a.name }));
    else if (a.mime === "application/pdf") mount(stage, h("iframe", { src, title: a.name }));
    else if (a.mime === "text/plain") {
      mount(stage, h("div", { class: "loading" }, "Loading…"));
      try { const res = await api(src, { raw: true }); const t = await res.text(); mount(stage, h("pre", { class: "text-view" }, t.slice(0, 500000))); } catch (e) { mount(stage, h("p", null, e.message)); }
    }
  };
  const go = (d) => { i = (i + d + list.length) % list.length; paint(); };
  const onKey = (e) => { if (e.key === "ArrowLeft") go(-1); else if (e.key === "ArrowRight") go(1); };
  document.addEventListener("keydown", onKey);
  let sx = null;
  stage.addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; }, { passive: true });
  stage.addEventListener("touchend", (e) => { if (sx === null) return; const dx = e.changedTouches[0].clientX - sx; if (Math.abs(dx) > 60 && list.length > 1) go(dx < 0 ? 1 : -1); sx = null; });
  openModal("", h("div", { class: "viewer" }, stage, prev, next, h("div", { class: "row viewer-bar" }, caption, h("span", { class: "spacer" }), dl)),
    { full: true, dark: true, noFocus: true, onClose: () => document.removeEventListener("keydown", onKey) });
  paint();
}
