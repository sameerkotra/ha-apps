"use strict";
/* Household Docs — the other household apps (SPEC §17.15–§17.16, APP_MESSAGES_SPEC §6.3–§6.5):
   ⋯ → Send to chat… (documents, folders, files) and, on checklists, ⋯ → Make a Todo list… (also: send the items you
   tick to an existing list). Each asks Household Chat / Household Todo through the app bus; the page polls the
   request until the other app answers (a spinner meanwhile), and says plainly when it isn't there, doesn't answer
   or says no. The checklist shows a note of where its items went. Offered only while the other app said hello
   (me.apps); never in Kids' space for chats. Everything from the server goes in with textContent. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, spinner, fmtWhen, titleOf } = D;
  const { mount } = UI;
  const can = (it, role) => ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[it.role] || 0) >= ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[role] || 9);
  const apps = () => (D.state.me && D.state.me.apps) || {};

  /** Poll a request until it's answered: resolves with the request (state done / failed / pending after `ms`). */
  async function waitFor(rid, ms, onTick) {
    const until = Date.now() + ms;
    for (;;) {
      const r = await api(`api/bus/requests/${encodeURIComponent(rid)}`);
      if (onTick) onTick(r);
      if (r.state !== "pending" || Date.now() > until) return r;
      await new Promise((res) => setTimeout(res, 700));
    }
  }
  D.waitForRequest = waitFor;

  function waiting(text) {
    return h("div", { class: "bus-wait", role: "status" }, h("span", { class: "spinner inline" }), " ", text);
  }

  // =====================================================================
  // Send to chat (§17.15)
  // =====================================================================
  D.addAction({ id: "send-chat", icon: "💬", label: "Send to chat…", order: 52,
    show: (it, ctx) => !!apps().chat && !D.isChild() && !it.inTrash && !it.isRoot && !(ctx && ctx.inFolderHead && !it.id),
    run: (it) => sendToChat(it) });

  function chatIcon(c) {
    if (c.kind === "personal") return "📌";
    if (c.household) return "🏡";
    if (c.kind === "direct") return "👤";
    return c.icon || "👥";
  }

  async function sendToChat(it) {
    const body = h("div", { class: "bus-box", id: "chatSend" }, waiting("Asking Household Chat for your chats…"));
    const m = openModal(`Send “${titleOf(it)}” to a chat`, body, { focus: false });
    const err = (msg, retry) => mount(body, h("div", { class: "error-text", id: "chatError", role: "alert" }, msg),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Close"),
        retry ? h("button", { class: "btn-secondary", type: "button", onclick: retry }, "Try again") : null));
    async function load() {
      mount(body, waiting("Asking Household Chat for your chats…"));
      let req;
      try { req = await api(`api/nodes/${encodeURIComponent(it.id)}/chat/chats`, { method: "POST" }); }
      catch (e) { err(e.message); return; }
      let r;
      try { r = await waitFor(req.request, (req.expiresIn || 120) * 1000 + 15000); } catch (e) { err(e.message, load); return; }
      if (!document.body.contains(body)) return;
      if (r.state === "failed") { err(r.error || "Household Chat didn't answer.", load); return; }
      if (r.state === "pending") { err("Household Chat didn't answer in time — is it running?", load); return; }
      pick(r.result);
    }
    function pick(res) {
      const chats = res.chats || [];
      if (!chats.length) { err("You aren't in any chat you can post in yet."); return; }
      let chosen = null, members = null, ask = 0;
      const mayShare = can(it, "manager") && !!it.ownerId;
      const shareBox = h("input", { type: "checkbox", id: "chatShare" });
      const role = h("select", { id: "chatShareRole", "aria-label": "Their access", disabled: true },
        h("option", { value: "viewer" }, "Can view"), h("option", { value: "editor" }, "Can edit"));
      const who = h("div", { class: "chat-members", id: "chatMembers" });
      const note = h("div", { class: "error-text", role: "alert" });
      // who is in the chosen chat: asked from Chat for that one chat only (security review: the list names nobody)
      async function loadMembers() {
        members = null;
        if (!chosen || !shareBox.checked) { mount(who); return; }
        const mine = ++ask;
        mount(who, waiting(`Asking who is in ${chosen.name || "that chat"}…`));
        let req, r;
        try {
          req = await api(`api/nodes/${encodeURIComponent(it.id)}/chat/chats`, { method: "POST", body: { chatId: chosen.id } });
          r = await waitFor(req.request, (req.expiresIn || 120) * 1000 + 15000);
        } catch (e) { if (mine === ask) mount(who, h("div", { class: "error-text" }, e.message)); return; }
        if (mine !== ask || !document.body.contains(who)) return;
        if (r.state !== "done") { mount(who, h("div", { class: "error-text", id: "chatMembersError" }, r.state === "failed" ? r.error : "Household Chat didn't answer in time — is it running?")); return; }
        members = { request: r.id, people: (r.result.cantOpen || []).filter((p) => !p.off), picked: new Set() };
        members.people.forEach((p) => members.picked.add(p.id));
        const outside = r.result.notInDocs ? h("p", { class: "hint" }, `${r.result.notInDocs} member${r.result.notInDocs === 1 ? " doesn't" : "s don't"} use Docs.`) : null;
        mount(who, members.people.length
          ? [h("p", { class: "hint" }, "These members can't open it yet — tick who gets access:"),
            h("div", { class: "chat-member-list" }, members.people.map((p) => h("label", { class: "mini-toggle", dataset: { member: p.id } },
              h("input", { type: "checkbox", checked: true, onchange: (e) => { if (e.target.checked) members.picked.add(p.id); else members.picked.delete(p.id); } }), p.name))), outside]
          : [h("p", { class: "hint" }, "Everyone in this chat who uses Docs can open it already."), outside]);
      }
      shareBox.addEventListener("change", () => { role.disabled = !shareBox.checked; loadMembers(); });
      const rows = chats.map((c) => {
        const radio = h("input", { type: "radio", name: "chatPick", value: c.id, onchange: () => { chosen = c; if (shareBox.checked) loadMembers(); } });
        return h("label", { class: "chat-pick", dataset: { chat: c.id } }, radio,
          h("span", { class: "chat-icon", "aria-hidden": "true" }, chatIcon(c)),
          h("span", { class: "row-text" }, h("span", { class: "row-name" }, c.name || "Chat"),
            h("span", { class: "row-meta" }, c.kind === "personal" ? "Only you" : `${c.members} member${c.members === 1 ? "" : "s"}`)));
      });
      const send = h("button", { class: "btn-primary", type: "button", id: "chatSendBtn", onclick: async () => {
        if (!chosen) { note.textContent = "Choose a chat."; return; }
        const share = mayShare && shareBox.checked ? role.value : null;
        if (share && !members) { note.textContent = "Wait until Household Chat says who is in that chat."; return; }
        send.disabled = true;
        note.textContent = "";
        const body = { chatId: chosen.id, share };
        if (share) Object.assign(body, { members: members.request, memberIds: Array.from(members.picked) });
        let req;
        try { req = await api(`api/nodes/${encodeURIComponent(it.id)}/chat/card`, { method: "POST", body }); }
        catch (e) { note.textContent = e.message; send.disabled = false; return; }
        mount(status, waiting(`Posting in ${chosen.name}…`));
        let r;
        try { r = await waitFor(req.request, 20000); } catch (e) { note.textContent = e.message; send.disabled = false; return; }
        if (r.state === "failed") { mount(status); note.textContent = r.error; send.disabled = false; return; }
        m.close();
        if (r.state === "pending") { toast("Sent — Household Chat posts it as soon as it answers.", false, { ms: 6000 }); return; }
        const res = r.result || {};
        const extra = share ? (res.shared ? ` — ${res.shared} ${res.shared === 1 ? "person" : "people"} can open it now` : "") +
          (res.notShared && res.notShared.length ? ` (not shared with ${res.notShared.join(", ")})` : "") : "";
        toast(`Posted in ${chosen.name}${extra}`, false, { ms: 6000 });
        if (share) D.render();
      } }, "Send");
      const status = h("div", { class: "bus-status" });
      mount(body,
        h("div", { class: "chat-list", role: "radiogroup", "aria-label": "Chats", id: "chatList" }, rows),
        res.more ? h("p", { class: "hint" }, "Your most recent chats are listed.") : null,
        mayShare ? h("div", { class: "share-members" }, h("label", { class: "mini-toggle" }, shareBox, "Also give the chat's members access"), role, who,
          h("p", { class: "hint" }, "A normal share (you can change or remove it in Share…), added once Chat has posted the card.")) : null,
        h("p", { class: "hint" }, "Chat shows a card with the title, the type and whose it is, with Open in Docs — never what's in it. Anyone who can't open it gets Docs' own “🔒 No access” page, which shows nothing about it."),
        status, note,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), send));
    }
    load();
  }

  // =====================================================================
  // Checklist → Todo (§17.16)
  // =====================================================================
  D.addAction({ id: "send-todo", icon: "✅", label: "Make a Todo list…", order: 53,
    show: (it) => !!apps().todo && it.kind === "checklist" && !it.inTrash, run: (it, ctx) => todoDialog(it, ctx) });

  async function todoDialog(it, ctx) {
    const body = h("div", { class: "bus-box", id: "todoSend" }, spinner());
    const m = openModal(`Send “${titleOf(it)}” to Todo`, body, { wide: true, focus: false });
    let doc;
    try { doc = it.items ? it : await api(`api/docs/${encodeURIComponent(it.id)}`); }
    catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
    if (!doc.items) { mount(body, h("div", { class: "error-text" }, "This isn't a checklist any more.")); return; }
    const items = doc.items;
    const picked = new Set(items.filter((x) => !x.done).map((x) => x.key));
    const incl = h("input", { type: "checkbox", id: "todoIncludeTicked" });
    const listBox = h("div", { class: "todo-items", id: "todoItems" });
    function drawItems() {
      mount(listBox, items.length ? items.map((x) => {
        const off = x.done && !incl.checked;
        return h("label", { class: "todo-item" + (x.level ? " indent" : "") + (x.done ? " done" : ""), dataset: { key: x.key } },
          h("input", { type: "checkbox", checked: picked.has(x.key) && !off, disabled: off, onchange: (e) => { if (e.target.checked) picked.add(x.key); else picked.delete(x.key); } }),
          h("span", null, x.text));
      }) : h("div", { class: "empty" }, "This checklist has no items."));
    }
    incl.addEventListener("change", () => { if (incl.checked) items.filter((x) => x.done).forEach((x) => picked.add(x.key)); drawItems(); });
    drawItems();
    // where: a new list (default) or an existing one (asked from Todo when chosen)
    const whereNew = h("input", { type: "radio", name: "todoWhere", value: "new", checked: true, id: "todoWhereNew" });
    const whereOld = h("input", { type: "radio", name: "todoWhere", value: "old", id: "todoWhereOld" });
    const newName = h("input", { type: "text", id: "todoNewName", maxlength: "60", value: titleOf(doc).slice(0, 60), "aria-label": "New list's name" });
    const newShared = h("select", { id: "todoNewShared", "aria-label": "Who sees the new list" },
      h("option", { value: "shared" }, "Shared with the household"), h("option", { value: "personal" }, "Just me (a personal list)"));
    const oldBox = h("div", { class: "todo-lists", id: "todoLists", hidden: true });
    let lists = null, listSel = null;
    async function loadLists() {
      mount(oldBox, waiting("Asking Household Todo for your lists…"));
      let req, r;
      try {
        req = await api(`api/docs/${encodeURIComponent(doc.id)}/todo/lists`, { method: "POST" });
        r = await waitFor(req.request, (req.expiresIn || 120) * 1000 + 15000);
      } catch (e) { mount(oldBox, h("div", { class: "error-text" }, e.message)); return; }
      if (r.state !== "done") {
        mount(oldBox, h("div", { class: "error-text", id: "todoListsError" }, r.state === "failed" ? r.error : "Household Todo didn't answer in time — is it running?"),
          h("button", { class: "btn-ghost btn-small", type: "button", onclick: loadLists }, "Try again"));
        return;
      }
      lists = r.result.lists || [];
      if (!lists.length) { mount(oldBox, h("div", { class: "hint" }, "You have no lists in Todo yet — make a new one.")); return; }
      listSel = h("select", { id: "todoListSel", "aria-label": "Todo list" }, lists.map((l) => h("option", { value: l.id },
        `${l.kind === "personal" ? "🔒 " : ""}${l.name}${l.maintenance ? " (maintenance)" : ""}${l.open !== null && l.open !== undefined ? ` — ${l.open} open` : ""}`)));
      mount(oldBox, listSel);
    }
    const syncWhere = () => {
      oldBox.hidden = !whereOld.checked;
      newName.disabled = newShared.disabled = whereOld.checked;
      if (whereOld.checked && lists === null) loadLists();
    };
    whereNew.addEventListener("change", syncWhere);
    whereOld.addEventListener("change", syncWhere);
    const keep = h("input", { type: "radio", name: "todoMove", value: "keep", checked: true, id: "todoKeep" });
    const move = h("input", { type: "radio", name: "todoMove", value: "move", id: "todoMove", disabled: !can(doc, "editor") || !!doc.readOnlyMode });
    const note = h("div", { class: "error-text", role: "alert", id: "todoError" });
    const status = h("div", { class: "bus-status", id: "todoStatus" });
    const sendBtn = h("button", { class: "btn-primary", type: "button", id: "todoSendBtn", onclick: async () => {
      note.textContent = "";
      const keys = items.filter((x) => picked.has(x.key) && (incl.checked || !x.done)).map((x) => x.key);
      if (!keys.length) { note.textContent = "Tick the items to send."; return; }
      const body2 = { keys, includeTicked: incl.checked, move: move.checked };
      if (whereOld.checked) {
        if (!listSel) { note.textContent = "Choose a list (or make a new one)."; return; }
        const l = lists.find((x) => x.id === listSel.value);
        Object.assign(body2, { listId: l.id, listKind: l.kind, listName: l.name });
      } else {
        const name = newName.value.trim();
        if (!name) { note.textContent = "Name the new list."; return; }
        body2.new = { name, shared: newShared.value === "shared" };
      }
      sendBtn.disabled = true;
      let req;
      try { req = await api(`api/docs/${encodeURIComponent(doc.id)}/todo`, { method: "POST", body: body2 }); }
      catch (e) { note.textContent = e.message; sendBtn.disabled = false; return; }
      mount(status, waiting(`Sending ${req.items} item${req.items === 1 ? "" : "s"}${req.parts > 1 ? ` in ${req.parts} parts` : ""}…`));
      let r;
      try {
        r = await waitFor(req.request, 60000, (x) => { if (x.progress && req.parts > 1) mount(status, waiting(`Sending… ${x.progress.sent} of ${x.progress.total}`)); });
      } catch (e) { note.textContent = e.message; sendBtn.disabled = false; return; }
      if (r.state === "failed") { mount(status); note.textContent = r.error; sendBtn.disabled = false; if (ctx && ctx.inEditor) D.render(); return; }
      m.close();
      if (r.state === "pending") { toast("Sending — Household Todo adds them as soon as it answers.", false, { ms: 6000 }); return; }
      const res = r.result || {};
      toast(`Sent to Todo › ${res.listName || "the list"}, ${res.sent} item${res.sent === 1 ? "" : "s"}` + (res.move ? ` — ${res.removed} taken out of this checklist` : ""), false, { ms: 6000 });
      if (D.state.route === "doc") D.render();
    } }, "Send to Todo");
    mount(body,
      h("div", { class: "todo-where" }, h("h4", null, "Where"),
        h("label", { class: "mini-toggle" }, whereNew, "A new list"), h("div", { class: "form-row" }, newName, newShared),
        h("label", { class: "mini-toggle" }, whereOld, "A list I already have"), oldBox),
      h("div", { class: "todo-what" }, h("h4", null, "Items"),
        h("div", { class: "form-row" }, h("label", { class: "mini-toggle" }, incl, "Include ticked items"),
          h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => { items.forEach((x) => { if (incl.checked || !x.done) picked.add(x.key); }); drawItems(); } }, "All"),
          h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => { picked.clear(); drawItems(); } }, "None")),
        listBox),
      h("div", { class: "todo-keep" }, h("h4", null, "Here in Docs"),
        h("label", { class: "mini-toggle" }, keep, "Keep them here too"),
        h("label", { class: "mini-toggle" }, move, "Move them (they leave this checklist once Todo has them)"),
        !can(doc, "editor") ? h("p", { class: "hint" }, "You can only view this checklist, so its items stay here.") : null),
      h("p", { class: "hint" }, "Todo makes the tasks (items under an item become its checklist). Dates, who does what and reminders are set in Todo. Only the items' text is sent."),
      status, note,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), sendBtn));
  }

  Object.assign(D, { sendToChat, todoDialog });

  // the note on a checklist: where its items went (§17.16)
  D.headExtras = D.headExtras || [];
  D.headExtras.push((doc) => {
    if (!doc || doc.kind !== "checklist" || !doc.todoSends || !doc.todoSends.length) return null;
    return h("div", { class: "head-note todo-note", id: "todoNote" }, doc.todoSends.map((s) => h("div", null,
      s.state === "failed" ? `⚠ ${s.by === "You" ? "Your" : s.by + "'s"} send to Todo didn't finish${s.error ? ": " + s.error : ""}`
        : `✅ ${s.by === "You" ? "" : s.by + " · "}Sent to Todo › ${s.listName || "a personal list"}, ${s.items} item${s.items === 1 ? "" : "s"}` +
          (s.move ? " (moved)" : "") + (s.state === "sending" ? " — still sending" : ""),
      h("span", { class: "hint" }, " · " + fmtWhen(s.at)))));
  });
})();
