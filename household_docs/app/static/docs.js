"use strict";
/* Household Docs — the editors (notes, checklists) and the item dialogs: share, move, copy, transfer, rename,
   history, "where it's stored", delete. Registered on window.Docs (app.js): the "doc" route, the ⋯ actions and
   Docs.shareDialog. A later step adds its editors with Docs.editors[kind] = render(page, doc, ctx). */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, pageHead, fmtSize, fmtWhen, fmtFull, titleOf, go } = D;
  const { $, mount, debounce, lsGet, lsSet } = UI;
  D.editors = D.editors || {};

  // =====================================================================
  // ⋯ actions
  // =====================================================================
  const can = (it, role) => ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[it.role] || 0) >= ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[role]);
  const isMine = (it) => it.role === "owner";
  const after = (msg) => { if (msg) toast(msg); D.render(); };

  D.addAction({ id: "open", label: "Open", icon: "↗", order: 0, show: (it, ctx) => !ctx.inEditor && !ctx.inFolderHead, run: (it) => D.openItem(it) });
  D.addAction({ id: "show", label: "Show in folder", icon: "📂", order: 5, show: (it, ctx) => !!ctx.inSearch && !it.inTrash,
    run: (it) => {
      if (!it.parentRef && it.ownerId && it.ownerId !== D.state.me.id) { go("#/shared"); return; }
      go("#/folder/" + encodeURIComponent(it.parentRef || "mine") + "/sel/" + encodeURIComponent(it.id));
    } });
  D.addAction({ id: "favourite", order: 10, icon: "⭐", label: (it) => (it.favourite ? "Remove from Favourites" : "Add to Favourites"),
    run: async (it) => { try { await api(`api/nodes/${it.id}/favourite`, { method: "POST", body: { value: !it.favourite } }); it.favourite = !it.favourite; after(it.favourite ? "Added to Favourites" : "Removed from Favourites"); } catch (e) { fail(e); } } });
  D.addAction({ id: "rename", label: "Rename…", icon: "✏️", order: 20, show: (it) => can(it, "editor"), run: (it) => renameDialog(it) });
  D.addAction({ id: "move", label: "Move…", icon: "📂", order: 30, show: (it) => can(it, "editor"), run: (it) => moveDialog(it) });
  D.addAction({ id: "copy", label: "Make a copy in My docs", icon: "⧉", order: 40, run: async (it) => {
    try {
      const c = await api(`api/nodes/${it.id}/copy`, { method: "POST", body: {} });
      toast(`Copied to My docs as ${c.name}`, false, { onclick: () => D.openItem(c), ms: 5000 });
      if (D.state.route === "mine") D.render();
    } catch (e) { fail(e); }
  } });
  D.addAction({ id: "share", label: "Share…", icon: "👥", order: 50, show: (it) => can(it, "manager") && it.ownerId && !D.isChild(), run: (it) => shareDialog(it) });
  D.addAction({ id: "transfer", label: "Transfer ownership…", icon: "🤝", order: 60, show: (it) => isMine(it) && !D.isChild(), run: (it) => transferDialog(it) });
  D.addAction({ id: "download", label: (it) => (it.kind === "folder" ? "Download (.zip)" : "Download"), icon: "⬇", order: 70, run: (it) => D.download(it) });
  D.addAction({ id: "history", label: (it) => (it.document ? "History" : "Earlier copies"), icon: "🕘", order: 75, show: (it) => it.kind !== "folder", run: (it) => historyDialog(it) });
  D.addAction({ id: "where", label: "Where it's stored", icon: "📍", order: 80, run: (it) => whereDialog(it) });
  D.addAction({ id: "sep1", sep: true, order: 84, show: (it) => !isMine(it) });
  D.addAction({ id: "leave", label: "Leave", icon: "🚪", order: 85, show: (it, ctx) => !isMine(it) && ctx.space === "shared",
    run: async (it) => {
      if (!(await confirmDialog("Leave", `Stop seeing “${it.name}”? ${it.ownerName || "Its owner"} can share it with you again.`, "Leave"))) return;
      try { await api(`api/nodes/${it.id}/leave`, { method: "POST" }); after("Left"); } catch (e) { fail(e); }
    } });
  D.addAction({ id: "hide", order: 86, icon: "🙈", label: (it) => (it.hidden ? "Show again" : "Hide"), show: (it, ctx) => !isMine(it) && (ctx.space === "shared" || ctx.space === "everyone"),
    run: async (it) => { try { await api(`api/nodes/${it.id}/hide`, { method: "POST", body: { value: !it.hidden } }); after(it.hidden ? "Shown again" : "Hidden — tick “Show hidden” to see it"); } catch (e) { fail(e); } } });
  D.addAction({ id: "sep2", sep: true, order: 98, show: (it) => can(it, "editor") });
  D.addAction({ id: "delete", label: "Delete", icon: "🗑", order: 99, danger: true, show: (it) => can(it, "editor"), run: (it, ctx) => deleteItem(it, ctx) });

  async function renameDialog(it, onDone) {
    const name = await D.askName("Rename", it.kind === "folder" ? "Folder name" : "Name", titleOf(it), "Rename");
    if (!name || name === titleOf(it)) return;
    try {
      const r = await api(`api/nodes/${it.id}/rename`, { method: "POST", body: { name } });
      if (onDone) onDone(r); else after("Renamed");
    } catch (e) { fail(e); }
  }
  async function deleteItem(it, ctx) {
    const what = it.kind === "folder" ? "This folder and everything in it go" : "It goes";
    const where = it.rootKind === "shared" ? `the Trash of “${it.rootLabel || "the shared folder"}” (for everyone who can change it)`
      : `${it.ownerId === D.state.me.id ? "your" : (it.ownerName || "its owner") + "'s"} Trash`;
    if (!(await confirmDialog("Delete", `Delete “${it.name}”? ${what} to ${where}, where it can be restored.`, "Delete", true))) return;
    try {
      await api(`api/nodes/${it.id}`, { method: "DELETE" });
      toast("Moved to Trash");
      if (ctx && (ctx.inEditor || ctx.inFolderHead)) go(D.folderHash(it.parentRef !== undefined ? it.parentRef : it.parentId)); else D.render();
    } catch (e) { fail(e); }
  }

  // ---------- move (a folder picker; one item or several from the same place) ----------
  function moveDialog(itOrList, onDone) {
    const list = Array.isArray(itOrList) ? itOrList : [itOrList];
    const it = list[0];
    const body = h("div", { class: "picker" });
    const err = h("div", { class: "error-text", role: "alert" });
    let at = it.parentRef !== undefined ? it.parentRef : it.parentId;
    let canHere = true;
    const title = list.length === 1 ? `Move “${it.name}”` : `Move ${list.length} items`;
    const m = openModal(title, h("div", null, body, err, h("div", { class: "actions" },
      h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
      h("button", { class: "btn-primary", type: "button", id: "moveHere", onclick: async () => {
        if (!canHere) { err.textContent = "You can't add things to this folder."; return; }
        const failed = [];
        for (const x of list) {
          try { await api(`api/nodes/${x.id}/move`, { method: "POST", body: { parentId: at } }); }
          catch (e) { failed.push(`${x.name}: ${e.message}`); }
        }
        if (failed.length && failed.length === list.length) { err.textContent = failed.join(" · "); return; }
        m.close();
        if (failed.length) toast(failed.join(" · "), true);
        if (onDone) onDone(); else after(list.length === 1 ? "Moved" : `Moved ${list.length - failed.length} items`);
      } }, "Move here"))), { focus: false });
    async function show(ref) {
      mount(body, spinner());
      let d;
      try { d = await api("api/folders" + (ref ? `?node=${encodeURIComponent(ref)}` : "")); }
      catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
      at = d.ref; canHere = d.canEdit;
      const crumbs = h("div", { class: "crumbs small" }, d.crumbs.map((c, i) => [i ? h("span", { class: "crumb-sep" }, "›") : null,
        (c.id || c.space === "mine") && i < d.crumbs.length - 1 ? h("button", { class: "link-btn", type: "button", onclick: () => show(c.id) }, c.name) : h("span", { class: "crumb current" }, c.name)]));
      const ids = new Set(list.map((x) => x.id));
      const rows = d.folders.filter((f) => !ids.has(f.id)).map((f) => h("button", { class: "picker-row", type: "button", onclick: () => show(f.id) }, "📁 ", f.name, h("span", { class: "picker-go" }, "›")));
      mount(body, crumbs, rows.length ? h("div", { class: "picker-list" }, rows) : h("div", { class: "empty" }, "No folders here."),
        canHere ? null : h("p", { class: "hint" }, "You can only view this folder."));
    }
    show(at);
  }

  // ---------- transfer ----------
  async function transferDialog(it) {
    let ppl;
    try { ppl = (await D.people()).filter((p) => !p.you); } catch (e) { fail(e); return; }
    if (!ppl.length) { toast("Nobody else uses Household Docs yet.", true); return; }
    const sel = h("select", { "aria-label": "New owner", id: "transferTo" }, ppl.map((p) => h("option", { value: p.id }, p.name)));
    const err = h("div", { class: "error-text", role: "alert" });
    const m = openModal("Transfer ownership", h("div", null,
      h("p", null, `“${it.name}” moves into the new owner's folder${it.kind === "folder" ? " with everything in it" : ""}. Its shares, favourites and history come along, and you keep Can edit (you can leave it later).`),
      h("label", { class: "field" }, "New owner", sel), err,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", onclick: async () => {
          try { await api(`api/nodes/${it.id}/transfer`, { method: "POST", body: { userId: sel.value } }); m.close(); after(`Given to ${sel.selectedOptions[0].textContent}`); }
          catch (e) { err.textContent = e.message; }
        } }, "Transfer"))));
  }

  // ---------- where it's stored ----------
  async function whereDialog(it) {
    let info;
    try { info = await api(`api/nodes/${it.id}`); } catch (e) { fail(e); return; }
    openModal("Where it's stored", h("div", null,
      h("p", null, "This is an ordinary file in Home Assistant's /share folder:"),
      h("div", { class: "path-box" }, h("code", null, info.path), h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": "Copy the path", onclick: () => navigator.clipboard && navigator.clipboard.writeText(info.path).then(() => toast("Copied")) }, "⧉")),
      h("p", { class: "hint" }, "Anyone who can reach /share (Samba, the File editor, other apps, a backup that includes Share) can open and change it there. The app's sharing applies inside the app only.")));
  }

  // ---------- history ----------
  async function historyDialog(it, onRestored) {
    const body = h("div", null, spinner());
    const m = openModal(`History — ${it.name}`, body, { wide: true, focus: false });
    let d;
    try { d = await api(`api/docs/${it.id}/versions`); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
    if (!d.versions.length) {
      mount(body, h("div", { class: "empty" }, it.document ? "No earlier versions yet. One is kept when someone else edits it, at most every 5 minutes while you edit, and whenever it was changed outside the app."
        : "No earlier copies. When an upload replaces this file, the previous copy is kept here for 7 days."));
      return;
    }
    const view = h("pre", { class: "version-view", hidden: true });
    mount(body, h("div", { class: "item-list" }, d.versions.map((v) => h("div", { class: "item-row", dataset: { version: String(v.n) } },
      h("div", { class: "row-main static" }, h("span", { class: "row-text" },
        h("span", { class: "row-name" }, fmtFull(v.createdAt)),
        h("span", { class: "row-meta" }, [v.outside ? "changed outside the app" : (v.who || "someone"), fmtSize(v.size)].join(" · ")))),
      it.document && it.kind !== "sheet" ? h("button", { class: "btn-ghost btn-small", type: "button", onclick: async () => {
        try { const t = await api(`api/docs/${it.id}/versions/${v.n}`); view.hidden = false; view.textContent = t.text; } catch (e) { fail(e); }
      } }, "View") : null,
      h("a", { class: "btn-ghost btn-small", href: `api/docs/${encodeURIComponent(it.id)}/versions/${v.n}/file`, download: "" }, "Download"),
      d.canRestore ? h("button", { class: "btn-secondary btn-small", type: "button", onclick: async () => {
        if (!(await confirmDialog("Restore", `Put back the version from ${fmtFull(v.createdAt)}? What's there now is kept as a version.`, "Restore"))) return;
        try { await api(`api/docs/${it.id}/versions/${v.n}/restore`, { method: "POST" }); m.close(); toast("Restored"); if (onRestored) onRestored(); else D.render(); } catch (e) { fail(e); }
      } }, "Restore") : null))), view);
  }

  // ---------- share ----------
  async function shareDialog(it) {
    const body = h("div", { class: "share-box" }, spinner());
    const m = openModal(`Share “${it.name}”`, body, { focus: false });
    let ppl = [];
    try { ppl = await D.people(); } catch (e) { /* the list still shows */ }
    async function draw(data) {
      if (!data) {
        try { data = await api(`api/nodes/${it.id}/shares`); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
      }
      const err = h("div", { class: "error-text", role: "alert" });
      const tickable = it.kind === "checklist" || it.kind === "folder";
      const roleOptions = (current, everyone) => [h("option", { value: "viewer" }, "Can view"), h("option", { value: "editor" }, "Can edit"),
        data.canMakeManagers && !everyone ? h("option", { value: "manager" }, "Manager") : (current === "manager" ? h("option", { value: "manager" }, "Manager") : null)];
      const send = async (userId, role, viewersTick) => {
        try { draw(await api(`api/nodes/${it.id}/shares`, { method: "POST", body: { userId, role, viewersTick } })); }
        catch (e) { err.textContent = e.message; }
      };
      const rows = data.shares.map((s) => {
        const sel = h("select", { "aria-label": `Role for ${s.name}`, value: s.role, disabled: !data.canShare }, roleOptions(s.role, s.userId === "*"));
        sel.addEventListener("change", () => send(s.userId, sel.value, s.viewersTick));
        const tick = tickable && s.role === "viewer" ? h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", checked: s.viewersTick, disabled: !data.canShare,
          onchange: (e) => send(s.userId, s.role, e.target.checked) }), "Viewers may tick items") : null;
        const leaveMine = s.userId === D.state.me.id;
        return h("div", { class: "share-row", dataset: { user: s.userId } },
          h("span", { class: "share-who" }, s.userId === "*" ? "👪 Everyone" : s.name, leaveMine ? h("span", { class: "badge-you" }, "you") : null,
            s.viaLink ? h("span", { class: "hint", title: "Opened the view link; goes if the link is turned off" }, " 🔗 by the link") : null),
          sel, tick,
          data.canShare || leaveMine ? h("button", { class: "icon-btn danger", type: "button", title: leaveMine ? "Leave" : "Remove", "aria-label": `Remove ${s.name}`, onclick: async () => {
            try { draw(await api(`api/nodes/${it.id}/shares/${encodeURIComponent(s.userId)}`, { method: "DELETE" })); if (leaveMine) { m.close(); D.render(); } }
            catch (e) { err.textContent = e.message; }
          } }, "✕") : null);
      });
      const taken = new Set(data.shares.map((s) => s.userId).concat([data.owner.id]));
      const choices = ppl.filter((p) => !taken.has(p.id));
      const who = h("select", { "aria-label": "Share with", id: "shareWho" },
        h("option", { value: "" }, "Choose a person…"),
        data.everyoneAllowed && !taken.has("*") ? h("option", { value: "*" }, "👪 Everyone") : null,
        choices.map((p) => h("option", { value: p.id }, p.name)));
      const role = h("select", { "aria-label": "Role", id: "shareRole" }, roleOptions());
      who.addEventListener("change", () => { if (who.value === "*") role.value = data.everyoneDefaultRole; });
      const add = data.canShare ? h("div", { class: "share-add" }, who, role,
        h("button", { class: "btn-primary", type: "button", id: "shareAdd", onclick: () => { if (!who.value) { err.textContent = "Choose who to share with."; return; } send(who.value, role.value, true); } }, "Share")) : null;
      const inherited = data.inherited.length ? h("div", { class: "share-inherited" }, h("div", { class: "hint" }, "From folders above (remove it there):"),
        data.inherited.map((s) => h("div", { class: "share-row inherited" }, h("span", { class: "share-who" }, s.userId === "*" ? "👪 Everyone" : s.name),
          h("span", { class: "chip" }, D.roleLabel(s.role)), h("span", { class: "hint" }, "from ", s.from)))) : null;
      mount(body,
        h("div", { class: "share-row owner" }, h("span", { class: "share-who" }, data.owner.name || "—"), h("span", { class: "chip on" }, "Owner")),
        rows, inherited, add, data.canShare ? linkBox(data, err) : null, err,
        h("p", { class: "hint" }, it.kind === "folder" ? "Everything inside this folder is shared too, including files added to it later. " : "",
          "Managers may share it further and remove people. People get a notification on their phone when something is shared with them."));
    }
    // "Anyone with the link can view" (§6.6)
    function linkBox(data, err) {
      const call = async (method, body) => {
        try {
          const d = await api(`api/nodes/${it.id}/link`, { method, body });
          if (method === "DELETE") { toast(d.removed ? `Link turned off — ${d.removed} ${d.removed === 1 ? "person" : "people"} who opened it can't any more` : "Link turned off"); draw(); }
          else draw();
        } catch (e) { err.textContent = e.message; }
      };
      if (!data.link) {
        return h("div", { class: "share-link", id: "shareLink" },
          h("button", { class: "btn-ghost", type: "button", id: "linkMake", onclick: () => call("POST", { new: false }) }, "🔗 Make a view link"),
          h("div", { class: "hint" }, "Anyone in the household who opens the link can view it — handy to send in a chat. You can turn it off any time."));
      }
      const url = viewLinkUrl(data.link.token);
      const field = h("input", { type: "text", readonly: true, value: url, id: "linkUrl", "aria-label": "View link", onfocus: (e) => e.target.select() });
      const copy = h("button", { class: "btn-primary btn-small", type: "button", id: "linkCopy", onclick: async () => {
        try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch (e) { field.focus(); field.select(); toast("Select the link and copy it.", true); }
      } }, "Copy");
      return h("div", { class: "share-link on", id: "shareLink" },
        h("div", { class: "share-link-head" }, h("strong", null, "🔗 Anyone with the link can view")),
        h("div", { class: "share-link-row" }, field, copy),
        h("div", { class: "share-link-row" },
          h("span", { class: "hint" }, data.link.opened ? `Opened by ${data.link.opened} ${data.link.opened === 1 ? "person" : "people"} so far.` : "Nobody has opened it yet."),
          h("button", { class: "btn-ghost btn-small", type: "button", id: "linkNew", title: "The old link stops working", onclick: () => call("POST", { new: true }) }, "New link"),
          h("button", { class: "btn-ghost btn-small danger", type: "button", id: "linkOff", onclick: () => call("DELETE") }, "Turn off")));
    }
    draw();
  }
  function viewLinkUrl(token) {
    const panel = D.state.me && D.state.me.apps && D.state.me.apps.panel;
    let origin = location.origin;
    try { if (window.parent !== window) origin = window.parent.location.origin; } catch (e) { /* not reachable */ }
    return panel ? `${origin}${panel}/view/${token}` : `${location.origin}${location.pathname}#/view/${token}`;
  }
  D.shareDialog = shareDialog;
  Object.assign(D, { renameDialog, moveDialog, historyDialog, transferDialog });

  // =====================================================================
  // The editor page: #/doc/<id>
  // =====================================================================
  const POLL_MS = 10000;
  let poller = null;
  function stopPoll() { if (poller) { clearInterval(poller); poller = null; } }

  D.route("doc", async (page, args, current) => {
    stopPoll();
    const id = args[0];
    mount(page, spinner());
    let doc;
    try { doc = await api(`api/docs/${encodeURIComponent(id)}`); }
    catch (e) {
      if (e.status === 404) { noAccessPage(page); return; }           // §17.15: e.g. a link from a chat card
      if (e.status === 415 || e.status === 413 || e.status === 422) {      // 422: a sheet that can't be read safely
        mount(page, pageHead("Document"), h("div", { class: "card" }, h("p", null, e.message),
          h("a", { class: "btn-primary", href: `api/nodes/${encodeURIComponent(id)}/file`, download: "" }, "Download")));
        return;
      }
      throw e;
    }
    if (!current()) return;
    D.state.currentSpace = doc.rootKind === "shared" ? "folders" : (doc.ownerId === D.state.me.id ? "mine" : "shared");
    const ed = D.editors[doc.kind] || D.editors.note;
    const atLine = args[1] === "at" ? parseInt(args[2], 10) || null : null;
    const atCell = args[1] === "cell" ? { tab: args[2], ref: args[3] } : null;
    ed(page, doc, { current, atLine, atCell, quick: args[1] === "quick" });
  });

  // 🔒 No access (§17.15): what any link to something you can't open shows. It says nothing about the item — not
  // its name, its owner or even whether it exists (the same page for a deleted one), as with "🔒 No access" links.
  function noAccessPage(page, what) {
    const noun = { folder: "folder", file: "file" }[what] || "document";     // from the link itself, not the server
    mount(page, pageHead("🔒 No access"), h("div", { class: "card no-access", id: "noAccess" },
      h("p", null, `This ${noun} isn't shared with you. Ask the person who sent it.`),
      h("p", { class: "hint" }, "Only the people it's shared with can open it — they can share it with you (Share…)."),
      h("div", { class: "actions left" }, h("a", { class: "btn-secondary", href: "#/" }, "Go to Home"))));
  }
  D.noAccessPage = noAccessPage;

  // Full screen (SPEC §13): the editor gets the whole window — the sidebar, the search bar and the bottom bar step
  // aside, and the browser's own full screen is asked for where it is allowed (inside Home Assistant's app it may not
  // be; the page still fills its frame). ⛶ again, Esc or leaving the document ends it.
  function setFull(on) {
    document.body.classList.toggle("doc-full", !!on);
    for (const b of document.querySelectorAll("#docFull")) {
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.title = on ? "Leave full screen (Esc)" : "Full screen";
    }
    try {
      if (on && document.fullscreenEnabled && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      else if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    } catch (e) { /* not allowed here: the page still fills its frame */ }
    window.dispatchEvent(new Event("resize"));        // the editors refit their height
    requestAnimationFrame(() => document.querySelectorAll(".note-text").forEach((el) => D.fitToScreen(el)));
  }
  D.setFull = setFull;
  D.isFull = () => document.body.classList.contains("doc-full");
  function fullButton() {
    const on = D.isFull();
    return h("button", { class: "icon-btn head-full", type: "button", id: "docFull", "aria-label": "Full screen",
      title: on ? "Leave full screen (Esc)" : "Full screen", "aria-pressed": on ? "true" : "false",
      onclick: () => setFull(!D.isFull()) }, "⛶");
  }
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !D.isFull() || e.defaultPrevented) return;
    if (document.querySelector("#modalRoot > *, .menu-pop, [role='menu']")) return;      // Esc closes those first
    setFull(false);
  });
  document.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement && D.isFull()) setFull(false); });

  function editorHead(doc, ctx) {
    const status = h("span", { class: "save-state", id: "saveState", role: "status", "aria-live": "polite" });
    const editing = h("span", { class: "chip warn editing-chip", id: "editingChip", hidden: !doc.editing }, doc.editing ? `${doc.editing} is editing` : "");
    const backHash = doc.parentRef ? D.folderHash(doc.parentRef) : (doc.ownerId === D.state.me.id ? "#/mine" : "#/shared");
    const back = h("a", { class: "icon-btn back-link", href: backHash, "aria-label": "Back to the folder", title: "Back to the folder" }, "←");
    // One line: ← | the name (takes what's left, "…" when long; the full name is its tooltip) | Share | ⋯ — so the editor
    // gets the height (SPEC §13). Everything else in the ⋯ menu as before; Rename is also there when the name is cut.
    const name = titleOf(doc);
    const title = doc.canEdit
      ? h("button", { class: "doc-title editable", type: "button", id: "docTitle", title: `${name} — Rename`, onclick: () => renameDialog(doc, () => D.render()) }, name)
      : h("h2", { class: "doc-title", id: "docTitle", title: name }, name);
    const actions = [fullButton()];
    if (doc.canShare) actions.push(h("button", { class: "btn-secondary btn-small head-share", type: "button", id: "docShare", title: "Share with people", onclick: () => shareDialog(doc) }, "Share"));
    actions.push(h("button", { class: "icon-btn head-more", type: "button", "aria-label": "More actions", title: "More actions", "aria-haspopup": "menu", id: "docMore", onclick: (e) => D.itemMenu(e.currentTarget, doc, { inEditor: true }) }, "⋯"));
    const role = doc.role !== "owner" ? h("span", { class: "chip role-" + doc.role }, D.roleLabel(doc.role, doc.rootKind)) : null;
    const subText = [doc.rootKind === "shared" ? `${doc.rootLabel} · ` : (doc.role !== "owner" && doc.ownerName ? `${doc.ownerName}'s · ` : ""),
      doc.modified ? `changed ${fmtWhen(doc.modified)}` : "", doc.updatedByName ? ` by ${doc.updatedByName}` : (doc.outside ? " outside the app" : "")].join("");
    const sub = h("span", { class: "doc-sub hint", title: subText }, subText);
    const extras = (D.headExtras || []).map((fn) => fn(doc, ctx)).filter(Boolean);
    return { el: h("div", { class: "doc-head-wrap" }, h("div", { class: "doc-head" },
      h("div", { class: "doc-head-row" }, back, title, h("div", { class: "head-actions doc-actions" }, actions)),
      h("div", { class: "doc-sub-row" }, role, sub, editing, status)), extras), status, editing };
  }
  D.editorHead = editorHead;
  // On a phone the note's text box reaches down to the bottom bar, so the height the one-line header frees goes to the
  // text (SPEC §13). Only grows (never below the CSS min-height); refits when the width changes (turning the phone),
  // not when the on-screen keyboard opens.
  function fitToScreen(el) {
    if (!matchMedia("(max-width: 760px)").matches || !document.body.contains(el) || !el.offsetParent) { el.style.minHeight = ""; return; }
    const bb = document.querySelector(".bottombar");
    const bottom = bb && getComputedStyle(bb).display !== "none" ? bb.getBoundingClientRect().top : window.innerHeight;
    const room = Math.floor(bottom - el.getBoundingClientRect().top - 10);
    el.style.minHeight = "";
    if (room > el.offsetHeight) el.style.minHeight = room + "px";
  }
  function keepFitted(el) {
    let w = window.innerWidth;
    const onResize = () => {
      if (!document.body.contains(el)) { window.removeEventListener("resize", onResize); return; }
      if (window.innerWidth !== w) { w = window.innerWidth; fitToScreen(el); }
    };
    window.addEventListener("resize", onResize);
    requestAnimationFrame(() => fitToScreen(el));
    return () => fitToScreen(el);
  }
  D.fitToScreen = fitToScreen;
  function setEditing(chip, who) { chip.hidden = !who; chip.textContent = who ? `${who} is editing` : ""; }

  // ---------- the secret hint (SPEC §3.3): a light check in the browser, nothing is sent ----------
  const { looksSecret, lineDiff } = window.DocsText;
  D.looksSecret = looksSecret;
  function secretHint() {
    if (!D.state.me.app.secretHint) return { el: null, check: () => {} };
    const warn = h("div", { class: "secret-warn", role: "status", hidden: true });
    const el = h("div", { class: "secret-hint" }, h("span", { class: "hint" }, "Docs are plain files — keep passwords and card numbers in Household Vault."), warn);
    const check = debounce((text) => {
      const what = looksSecret(text);
      warn.hidden = !what;
      warn.textContent = what ? `⚠ This looks like it holds ${what}. Anyone with access to /share can read docs — Household Vault keeps it encrypted.` : "";
    }, 600);
    return { el, check };
  }

  // ---------- line compare (Keep mine / Use theirs / Compare): DocsText.lineDiff ----------
  D.lineDiff = lineDiff;

  function conflictDialog(detail, mine) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; m.close(); resolve(v); } };
      const cmp = h("div", { class: "diff", hidden: true });
      const m = openModal("Changed since you opened it", h("div", null,
        h("p", null, detail.message, detail.by ? ` (${detail.by})` : ""),
        h("div", { class: "actions conflict-actions" },
          h("button", { class: "btn-ghost", type: "button", onclick: () => {
            cmp.hidden = !cmp.hidden;
            const d = lineDiff(detail.text || "", mine);
            mount(cmp, d ? d.map(([k, line]) => h("div", { class: "diff-line " + (k === "-" ? "theirs" : k === "+" ? "mine" : "") }, (k === "-" ? "− " : k === "+" ? "+ " : "  ") + line))
              : h("div", { class: "hint" }, "Too long to compare here."));
          } }, "Compare"),
          h("button", { class: "btn-secondary", type: "button", id: "useTheirs", onclick: () => finish("theirs") }, "Use theirs"),
          h("button", { class: "btn-primary", type: "button", id: "keepMine", onclick: () => finish("mine") }, "Keep mine")),
        h("p", { class: "hint" }, "Keep mine saves your text; theirs is kept under History. Use theirs drops your changes."), cmp),
      { wide: true, focus: false, onClose: () => { if (!done) { done = true; resolve(null); } } });
    });
  }

  // ---------- notes: plain text (.txt) and Markdown (.md, §17.2) ----------
  // Find & Replace (Ctrl+F), tappable links, [[links]] to other documents with a picker, Linked from, and for
  // Markdown: Edit / Preview / Split, a small toolbar, tick boxes that tick in the preview.
  const WEB_LINK = /\bhttps?:\/\/[^\s<>"]+/gi;
  function webLinks(text) {
    const out = [];
    for (const m of String(text || "").matchAll(WEB_LINK)) {
      const u = m[0].replace(/[.,;:!?)\]]+$/, "");
      const safe = window.DocsMd ? DocsMd.safeHref(u) : null;
      if (safe && !out.includes(u)) out.push(u);
      if (out.length >= 30) break;
    }
    return out;
  }
  function docHref(info) {
    if (!info || info.state !== "ok") return null;
    return (info.kind === "folder" ? "#/folder/" : "#/doc/") + encodeURIComponent(info.id);
  }

  /** The [[ picker: a document or folder the person can see; resolves with {title, id} or null. */
  function linkPicker() {
    return new Promise((resolve) => {
      let done = false;
      const input = h("input", { type: "search", id: "linkPickInput", placeholder: "Type a name", "aria-label": "Find a document", maxlength: "100", autocomplete: "off" });
      const list = h("div", { class: "picker-list", id: "linkPickList" });
      const finish = (v) => { if (!done) { done = true; m.close(); resolve(v); } };
      const m = openModal("Link to a document", h("div", { class: "link-picker" }, input, list,
        h("p", { class: "hint" }, "The link is written into the note as [[Name]] — readable in any editor — and keeps working if the document is renamed or moved.")),
      { focus: false, onClose: () => { if (!done) { done = true; resolve(null); } } });
      let seq = 0;
      const run = debounce(async () => {
        const q = input.value.trim();
        const my = ++seq;
        if (!q) { mount(list, h("div", { class: "hint" }, "Start typing a name.")); return; }
        let d;
        try { d = await api("api/search?" + new URLSearchParams({ q, match: "name", limit: "20" })); } catch (e) { mount(list, h("div", { class: "error-text" }, e.message)); return; }
        if (my !== seq) return;
        mount(list, d.results.length ? d.results.slice(0, 20).map((it) => h("button", { class: "picker-row", type: "button", dataset: { id: it.id },
          onclick: () => finish({ title: titleOf(it), id: it.id }) }, D.iconOf(it) + " ", titleOf(it), h("span", { class: "hint" }, " " + (it.location || ""))))
          : h("div", { class: "empty" }, "Nothing with that name."));
      }, 200);
      input.addEventListener("input", run);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); const b = list.querySelector("button"); if (b) b.click(); } });
      mount(list, h("div", { class: "hint" }, "Start typing a name."));
      setTimeout(() => input.focus(), 40);
    });
  }

  /** Find & Replace over a textarea. Returns {el, open()}. */
  function findBar(ta, canReplace, onChanged) {
    const find = h("input", { type: "search", id: "findInput", placeholder: "Find", "aria-label": "Find", maxlength: "500", autocomplete: "off" });
    const repl = h("input", { type: "text", id: "replaceInput", placeholder: "Replace with", "aria-label": "Replace with", maxlength: "500", hidden: !canReplace });
    const caseBox = h("input", { type: "checkbox", id: "findCase" });
    const count = h("span", { class: "hint find-count", id: "findCount", role: "status" });
    let at = -1;
    const matches = () => {
      const q = find.value;
      if (!q) return [];
      const hay = caseBox.checked ? ta.value : ta.value.toLowerCase();
      const needle = caseBox.checked ? q : q.toLowerCase();
      const out = [];
      let i = hay.indexOf(needle);
      while (i >= 0 && out.length < 10000) { out.push(i); i = hay.indexOf(needle, i + Math.max(1, needle.length)); }
      return out;
    };
    function show(dir) {
      const ms = matches();
      if (!ms.length) { count.textContent = find.value ? "Not found" : ""; at = -1; return; }
      const cur = ta.selectionStart;
      if (dir === 0) at = ms.findIndex((x) => x >= Math.min(cur, ta.selectionEnd === cur ? cur : cur));
      else if (dir > 0) at = ms.findIndex((x) => x > (at >= 0 && at < ms.length ? ms[at] : cur - 1));
      else { const prev = at >= 0 && at < ms.length ? ms[at] : cur; at = -1; for (let k = ms.length - 1; k >= 0; k--) if (ms[k] < prev) { at = k; break; } }
      if (at < 0) at = dir < 0 ? ms.length - 1 : 0;
      const s = ms[at];
      count.textContent = `${at + 1} of ${ms.length}`;
      const keep = document.activeElement;
      ta.focus({ preventScroll: true });
      ta.setSelectionRange(s, s + find.value.length);
      const lh = parseFloat(getComputedStyle(ta).lineHeight) || 22;
      const line = ta.value.slice(0, s).split("\n").length - 1;
      ta.scrollTop = Math.max(0, line * lh - ta.clientHeight / 3);
      if (keep && keep !== ta && keep.focus) keep.focus({ preventScroll: true });
    }
    function replaceOne() {
      const ms = matches();
      const s = ta.selectionStart, e = ta.selectionEnd;
      if (!ms.includes(s) || e - s !== find.value.length) { show(1); return; }
      ta.setRangeText(repl.value, s, e, "end");
      onChanged();
      at = -1;
      show(1);
    }
    function replaceAll() {
      const ms = matches();
      if (!ms.length) { toast("Nothing to replace"); return; }
      let out = "", last = 0;
      for (const s of ms) { out += ta.value.slice(last, s) + repl.value; last = s + find.value.length; }
      out += ta.value.slice(last);
      ta.value = out;
      onChanged();
      count.textContent = "";
      toast(`Replaced ${ms.length}`);
    }
    find.addEventListener("input", () => { at = -1; show(0); });
    find.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); show(e.shiftKey ? -1 : 1); } if (e.key === "Escape") { e.stopPropagation(); close(); } });
    repl.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); replaceOne(); } if (e.key === "Escape") { e.stopPropagation(); close(); } });
    caseBox.addEventListener("change", () => { at = -1; show(0); });
    const el = h("div", { class: "find-bar", id: "findBar", hidden: true, role: "search" },
      find, h("button", { class: "icon-btn", type: "button", title: "Previous (Shift+Enter)", "aria-label": "Previous", onclick: () => show(-1) }, "↑"),
      h("button", { class: "icon-btn", type: "button", title: "Next (Enter)", "aria-label": "Next", id: "findNext", onclick: () => show(1) }, "↓"), count,
      h("label", { class: "mini-toggle", title: "Match case" }, caseBox, "Aa"),
      canReplace ? [repl, h("button", { class: "btn-ghost btn-small", type: "button", id: "replaceOne", onclick: replaceOne }, "Replace"),
        h("button", { class: "btn-ghost btn-small", type: "button", id: "replaceAll", onclick: replaceAll }, "All")] : null,
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close find", title: "Close", onclick: () => close() }, "✕"));
    function close() { el.hidden = true; count.textContent = ""; ta.focus(); }
    function open() {
      el.hidden = false;
      const sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
      if (sel && !sel.includes("\n") && sel.length < 200) find.value = sel;
      find.focus(); find.select();
      if (find.value) show(0);
    }
    return { el, open };
  }

  D.editors.note = D.editors.markdown = function noteEditor(page, doc, ctx) {
    const head = editorHead(doc, ctx);
    const isMd = doc.kind === "markdown";
    const ta = h("textarea", { class: "note-text", id: "noteText", spellcheck: "true", readonly: !doc.canEdit, "aria-label": doc.name });
    ta.value = doc.text;
    const wrapOn = lsGet("docs.wrap") !== "0", monoOn = lsGet("docs.mono") === "1";
    const applyLook = () => { ta.classList.toggle("nowrap", lsGet("docs.wrap") === "0"); ta.classList.toggle("mono", lsGet("docs.mono") === "1"); };
    const hints = {};                                        // [[text]] → the id the picker chose (sent with saves)
    let linkInfo = { links: {}, backlinks: [] };
    const st = { etag: doc.etag, dirty: false, saving: null, sent: null, retry: null };
    const setStatus = (t, kind) => { head.status.textContent = t; head.status.className = "save-state" + (kind ? " " + kind : ""); };

    // ---- Markdown: preview, modes, toolbar
    const preview = h("div", { class: "md-body md-preview", id: "mdPreview", "aria-live": "off" });
    const narrow = () => matchMedia("(max-width: 760px)").matches;
    let mode = isMd ? (lsGet("docs.mdView") || (doc.canEdit ? (narrow() ? "edit" : "split") : "preview")) : "edit";
    if (isMd && !doc.canEdit && mode === "split") mode = "preview";
    if (isMd && narrow() && mode === "split") mode = "edit";
    function drawPreview() {
      if (!isMd || mode === "edit" || !window.DocsMd) return;
      mount(preview, DocsMd.render(DocsMd.parse(ta.value), document, {
        docLink: (t) => { const info = linkInfo.links[t]; return info ? Object.assign({ href: docHref(info), title: info.name }, info) : { state: "pending" }; },
        onTick: doc.canEdit ? (line) => { ta.value = DocsMd.toggleTask(ta.value, line); changed(); drawPreview(); } : null,
        images: true,
      }));
      for (const im of preview.querySelectorAll("img.md-pic")) {          // a picture this person can't see, or gone
        im.addEventListener("error", () => im.replaceWith(h("span", { class: "md-img", title: "This picture can't be shown — it was deleted or isn't shared with you." }, "🖼 " + (im.alt || "picture"))), { once: true });
      }
      if (!ta.value.trim()) preview.appendChild(h("p", { class: "hint" }, "Nothing here yet."));
    }
    const box = h("div", { class: "editor-card note-box mode-" + mode, id: "noteBox" }, ta, isMd ? preview : null);
    function setMode(m) {
      mode = m;
      lsSet("docs.mdView", m);
      box.className = "editor-card note-box mode-" + m;
      modeBtns.forEach((b) => { b.classList.toggle("on", b.dataset.mode === m); b.setAttribute("aria-pressed", String(b.dataset.mode === m)); });
      mdTools.hidden = m === "preview" || !doc.canEdit;
      drawPreview();
      refit();
    }
    let refit = () => {};
    const modeBtns = isMd ? [["edit", "Edit"], ["preview", "Preview"], ["split", "Split"]].map(([m, l]) => h("button", { type: "button", class: "chip-toggle" + (mode === m ? " on" : ""),
      dataset: { mode: m }, "aria-pressed": String(mode === m), id: "mdMode-" + m, onclick: () => setMode(m) }, l)) : [];
    function wrapSel(before, after, placeholder) {
      const s = ta.selectionStart, e = ta.selectionEnd;
      const sel = ta.value.slice(s, e) || placeholder;
      ta.setRangeText(before + sel + after, s, e, "end");
      ta.setSelectionRange(s + before.length, s + before.length + sel.length);
      ta.focus();
      changed();
    }
    function prefixLines(prefix, re) {
      const s = ta.selectionStart, e = ta.selectionEnd;
      const ls = ta.value.lastIndexOf("\n", s - 1) + 1;
      let le = ta.value.indexOf("\n", e);
      if (le < 0) le = ta.value.length;
      const block = ta.value.slice(ls, le).split("\n").map((l) => (re.test(l) ? l.replace(re, "") : prefix + l.replace(/^(#{1,6}\s+|[-*+]\s+(\[[ xX]\]\s+)?)/, ""))).join("\n");
      ta.setRangeText(block, ls, le, "end");
      ta.focus();
      changed();
    }
    // pointerdown kept from the button: the text keeps its focus and selection (and a phone its keyboard)
    const tbtn = (id, label, title, fn) => h("button", { class: "icon-btn md-tool", type: "button", id, title, "aria-label": title, onclick: fn,
      onpointerdown: (e) => e.preventDefault() }, label);
    const mdTools = h("div", { class: "md-tools", id: "mdTools", hidden: !isMd || mode === "preview" || !doc.canEdit, role: "toolbar", "aria-label": "Formatting" },
      tbtn("mdBold", "B", "Bold", () => wrapSel("**", "**", "bold")), tbtn("mdItalic", "I", "Italic", () => wrapSel("*", "*", "italic")),
      tbtn("mdHeading", "H", "Heading", () => prefixLines("## ", /^#{1,6}\s+/)), tbtn("mdList", "•", "Bulleted list", () => prefixLines("- ", /^[-*+]\s+(?!\[)/)),
      tbtn("mdCheck", "☐", "Tick box", () => prefixLines("- [ ] ", /^[-*+]\s+\[[ xX]\]\s+/)),
      tbtn("mdNumbered", "1.", "Numbered list", () => prefixLines("1. ", /^\d+[.)]\s+/)),
      tbtn("mdQuote", "❝", "Quote", () => prefixLines("> ", /^>\s?/)),
      tbtn("mdCode", "</>", "Code", () => wrapSel("`", "`", "code")),
      tbtn("mdLink", "🔗", "Web link", () => { const s = ta.value.slice(ta.selectionStart, ta.selectionEnd); wrapSel("[", "](https://)", s || "link text"); }),
      tbtn("mdDocLink", "[[ ]]", "Link to a document", () => insertLink(false)),
      tbtn("mdPicture", "🖼", "Add a picture", () => picInput.click()));

    // ---- pictures: pasted, or chosen (a phone's camera too) — kept as a file next to the note
    const picInput = h("input", { type: "file", accept: "image/png,image/jpeg,image/gif,image/webp", hidden: true, id: "mdPictureFile",
      onchange: () => { const f = picInput.files && picInput.files[0]; picInput.value = ""; if (f) addPicture(f); } });
    const PIC_TYPES = { "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp" };
    async function addPicture(file) {
      const ext = PIC_TYPES[file.type];
      if (!ext) { toast("Only PNG, JPEG, GIF and WebP pictures can go in a note.", true); return; }
      const now = new Date(), two = (n) => String(n).padStart(2, "0");
      const stamp = `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())} ${two(now.getHours())}.${two(now.getMinutes())}.${two(now.getSeconds())}`;
      const name = (file.name && !/^image\.\w+$/i.test(file.name) ? file.name : `Picture ${stamp}.${ext}`).replace(/[\[\]()\n]/g, " ");
      const at = ta.selectionStart, end = ta.selectionEnd;
      setStatus("Adding the picture…");
      let node;
      try {
        node = await api(`api/nodes/${encodeURIComponent(doc.parentRef || "mine")}/upload?name=${encodeURIComponent(name)}`,
          { method: "POST", rawBody: file, headers: { "Content-Type": "application/octet-stream" } });
      } catch (e) { setStatus(st.dirty ? "Unsaved changes…" : "Saved"); toast(`The picture couldn't be added: ${e.message}`, true); return; }
      const label = (node.name || name).replace(/\.\w+$/, "").replace(/[\[\]]/g, " ");
      let text;
      if (isMd) text = `![${label}](doc:${node.id})`;
      else { const t = (node.name || name).replace(/[\[\]|\n]/g, " ").trim(); hints[t] = node.id; linkInfo.links[t] = { state: "ok", id: node.id, name: node.name, kind: "file" }; text = `[[${t}]]`; }
      const lead = at > 0 && ta.value[at - 1] !== "\n" && isMd ? "\n" : "";
      ta.setRangeText(lead + text + (isMd ? "\n" : ""), at, end, "end");
      ta.focus();
      changed();
      toast(`Picture saved next to the note as ${node.name || name}`);
    }
    if (doc.canEdit) ta.addEventListener("paste", (e) => {
      const items = e.clipboardData ? Array.from(e.clipboardData.items || []) : [];
      const pic = items.find((it) => it.kind === "file" && PIC_TYPES[it.type]);
      if (!pic || (e.clipboardData.getData("text/plain") || "").trim()) return;     // text wins when both are there
      const f = pic.getAsFile();
      if (!f) return;
      e.preventDefault();
      addPicture(f);
    });

    // ---- links to other documents and Linked from
    const linksBox = h("div", { class: "links-box", id: "linksBox" });
    function drawLinks() {
      const web = webLinks(ta.value);
      const docLinks = window.DocsMd ? DocsMd.docLinks(ta.value) : [];
      const parts = [];
      if ((!isMd || mode === "edit") && (web.length || docLinks.length)) {
        parts.push(h("div", { class: "links-row", id: "noteLinks" }, h("span", { class: "links-label" }, "Links"),
          web.map((u) => h("a", { class: "md-link", href: DocsMd.safeHref(u), target: "_blank", rel: "noopener noreferrer nofollow", title: u }, u.replace(/^https?:\/\//, "").slice(0, 60))),
          docLinks.map((t) => {
            const info = linkInfo.links[t];
            const href = docHref(info);
            if (href) return h("a", { class: "doc-link", href, title: info.name }, t);
            const why = info ? { noaccess: "🔒 No access", deleted: "Deleted", missing: "Not found" }[info.state] : null;
            return h("span", { class: "doc-link " + (info ? info.state : "pending") }, t, why ? h("span", { class: "doc-link-why" }, " " + why) : null);
          })));
      }
      if (linkInfo.backlinks.length) {
        parts.push(h("div", { class: "links-row", id: "backlinks" }, h("span", { class: "links-label" }, "Linked from"),
          linkInfo.backlinks.map((b) => h("a", { class: "doc-link back", href: (b.kind === "folder" ? "#/folder/" : "#/doc/") + encodeURIComponent(b.id) }, "↩ " + b.title))));
      }
      mount(linksBox, parts);
    }
    let linkSeq = 0;
    async function loadLinks() {
      const my = ++linkSeq;
      try { const d = await api(`api/docs/${doc.id}/links`); if (my === linkSeq) { linkInfo = d; drawLinks(); drawPreview(); } } catch (e) { /* the links just stay plain */ }
    }

    // ---- tools row
    const find = findBar(ta, doc.canEdit, () => changed());
    const pickBtn = doc.canEdit ? h("button", { class: "btn-ghost btn-small", type: "button", id: "linkDoc", title: "Link to a document ([[)", onclick: () => insertLink(false) }, "[[ ]] Link") : null;
    const tools = h("div", { class: "doc-tools" },
      isMd ? h("span", { class: "chip-set md-modes", role: "group", "aria-label": "View" }, modeBtns) : null,
      h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", checked: wrapOn, onchange: (e) => { lsSet("docs.wrap", e.target.checked ? "1" : "0"); applyLook(); } }), "Wrap lines"),
      h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", checked: monoOn, onchange: (e) => { lsSet("docs.mono", e.target.checked ? "1" : "0"); applyLook(); } }), "Monospace"),
      h("button", { class: "btn-ghost btn-small", type: "button", id: "findBtn", title: "Find" + (doc.canEdit ? " and replace" : "") + " (Ctrl+F)", onclick: () => { if (isMd && mode === "preview") setMode(doc.canEdit ? "split" : "edit"); find.open(); } }, doc.canEdit ? "🔍 Find & replace" : "🔍 Find"),
      pickBtn,
      isMd ? h("span", { class: "chip" }, "Markdown") : null,
      !doc.canEdit ? h("span", { class: "chip" }, doc.readOnlyMode ? "Read only while documents are moved" : "Read only") : null);
    applyLook();
    const secret = secretHint();
    // on a phone the formatting buttons sit under the text, kept in view above the keyboard
    const phone = isMd && narrow();
    mdTools.classList.toggle("md-tools-phone", phone);
    mount(page, head.el, tools, phone ? null : mdTools, find.el, box, phone ? mdTools : null, picInput, linksBox, secret.el);
    refit = keepFitted(ta);
    secret.check(ta.value);
    setStatus(doc.canEdit ? "Saved" : "");
    drawPreview();
    drawLinks();
    loadLinks();

    async function insertLink(typed) {
      const pos = ta.selectionStart;
      const picked = await linkPicker();
      ta.focus();
      if (!picked) return;
      const start = typed && ta.value.slice(pos - 2, pos) === "[[" ? pos - 2 : pos;
      const end = typed && ta.value.slice(pos, pos + 2) === "]]" ? pos + 2 : pos;
      const text = picked.title.replace(/[\[\]|\n]/g, " ").trim();
      ta.setRangeText(`[[${text}]]`, start, end, "end");
      hints[text] = picked.id;
      linkInfo.links[text] = { state: "ok", id: picked.id, name: picked.title, kind: "note" };
      changed();
    }

    async function save(opts = {}) {
      if (!doc.canEdit || !st.dirty) return;
      if (st.saving) { st.again = true; return st.saving; }
      const text = ta.value;
      setStatus("Saving…");
      st.saving = (async () => {
        try {
          const links = {};
          Object.keys(hints).forEach((t) => { if (text.includes(`[[${t}]]`)) links[t] = hints[t]; });
          const body = { etag: st.etag, text, keepMine: !!opts.keepMine };
          if (Object.keys(links).length) body.links = links;
          const r = await api(`api/docs/${doc.id}`, { method: "PATCH", body, keepalive: !!opts.keepalive });
          st.etag = r.etag;
          if (ta.value === text) { st.dirty = false; setStatus("Saved"); } else setStatus("Unsaved changes…");
          if (text.includes("[[") && !opts.keepalive) loadLinks();
        } catch (e) {
          if (e.status === 409 && e.detail && typeof e.detail === "object") {
            const choice = await conflictDialog(e.detail, ta.value);
            if (choice === "mine") { st.etag = e.detail.etag; st.saving = null; st.dirty = true; return save({ keepMine: true }); }
            if (choice === "theirs") { ta.value = e.detail.text || ""; st.etag = e.detail.etag; st.dirty = false; setStatus("Saved"); secret.check(ta.value); drawPreview(); drawLinks(); }
            else setStatus("Not saved — changed elsewhere", "warn");
          } else if (!e.status) {
            setStatus("Offline — will retry", "warn");
            clearTimeout(st.retry); st.retry = setTimeout(() => save(), 5000);
          } else { setStatus("Not saved", "warn"); fail(e); }
        } finally {
          st.saving = null;
          if (st.again) { st.again = false; if (st.dirty) save(); }
        }
      })();
      return st.saving;
    }
    const soon = debounce(() => save(), 1000);
    const later = debounce(() => { drawPreview(); drawLinks(); }, 250);
    function changed() { st.dirty = true; setStatus("Unsaved changes…"); soon(); secret.check(ta.value); later(); }
    ta.addEventListener("input", (e) => {
      changed();
      if (doc.canEdit && e.inputType === "insertText" && e.data === "[" && ta.value.slice(ta.selectionStart - 2, ta.selectionStart) === "[[") insertLink(true);
    });
    ta.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) { e.preventDefault(); find.open(); }
      else if ((e.ctrlKey || e.metaKey) && (e.key === "h" || e.key === "H") && doc.canEdit) { e.preventDefault(); find.open(); }
      else if (isMd && doc.canEdit && (e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) { e.preventDefault(); wrapSel("**", "**", "bold"); }
      else if (isMd && doc.canEdit && (e.ctrlKey || e.metaKey) && (e.key === "i" || e.key === "I")) { e.preventDefault(); wrapSel("*", "*", "italic"); }
    });
    D.flushNote = async () => { if (st.dirty) await save(); };
    D.state.leaving = async () => {
      stopPoll();
      D.flushNote = null;
      if (st.dirty) await save();
      if (ctx.quick && doc.canEdit && D.finishQuickNote) await D.finishQuickNote(doc.id);
    };
    const onHide = () => { if (document.visibilityState === "hidden" && st.dirty) save({ keepalive: true }); };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", () => {
      if (st.dirty) save({ keepalive: true });
      if (ctx.quick && doc.canEdit && D.finishQuickNote && D.state.route === "doc") D.finishQuickNote(doc.id, true);
    }, { once: true });
    poller = setInterval(async () => {
      if (!ctx.current() || D.state.route !== "doc") { stopPoll(); document.removeEventListener("visibilitychange", onHide); return; }
      if (document.visibilityState !== "visible") return;
      try {
        const e = await api(`api/docs/${doc.id}/etag`);
        setEditing(head.editing, e.editing);
        if (e.etag !== st.etag && !st.dirty && !st.saving) {
          const fresh = await api(`api/docs/${doc.id}`);
          if (!st.dirty && !st.saving) {
            const pos = ta.selectionStart;
            ta.value = fresh.text !== undefined ? fresh.text : ta.value;
            st.etag = fresh.etag;
            try { ta.setSelectionRange(pos, pos); } catch (x) { /* ignore */ }
            drawPreview(); drawLinks();
            toast(e.updatedByName ? `Updated — ${e.updatedByName} changed it` : "Updated — it was changed outside the app");
          }
        }
      } catch (x) { if (x.status === 404) { stopPoll(); toast("This document isn't there any more.", true); } }
    }, POLL_MS);
    if (ctx.atLine) {
      if (isMd && mode === "preview") setMode("edit");
      const lines = ta.value.split("\n");
      const n = Math.min(ctx.atLine, lines.length) - 1;
      const start = lines.slice(0, n).reduce((a, l) => a + l.length + 1, 0);
      setTimeout(() => {
        ta.focus();
        try { ta.setSelectionRange(start, start + lines[n].length); } catch (x) { /* ignore */ }
        const lh = parseFloat(getComputedStyle(ta).lineHeight) || 24;
        ta.scrollTop = Math.max(0, n * lh - ta.clientHeight / 3);
        ta.scrollIntoView({ block: "center" });
      }, 60);
    } else if (doc.canEdit && matchMedia("(pointer: fine)").matches && mode !== "preview") ta.focus();
  };

  // Plain ↔ Markdown (§17.2): the file is renamed .txt ↔ .md; the item stays the same (shares, tags, history)
  D.addAction({ id: "convert-note", icon: "🔁", order: 76, label: (it) => (it.kind === "markdown" ? "Make it plain text (.txt)" : "Make it a Markdown note (.md)"),
    show: (it) => (it.kind === "note" || it.kind === "markdown") && can(it, "editor") && !(D.state.me && D.state.me.readOnly),
    run: async (it, ctx) => {
      const toMd = it.kind === "note";
      if (!(await confirmDialog(toMd ? "Make it a Markdown note" : "Make it plain text",
        toMd ? `“${titleOf(it)}” becomes ${titleOf(it)}.md: headings, lists and tick boxes show formatted in the preview. The text stays as it is.`
          : `“${titleOf(it)}” becomes ${titleOf(it)}.txt: the text stays as it is, shown without formatting.`, toMd ? "Make it Markdown" : "Make it plain text"))) return;
      try {
        if (ctx && ctx.inEditor && D.flushNote) await D.flushNote();
        await api(`api/docs/${encodeURIComponent(it.id)}/convert`, { method: "POST" });
        toast(toMd ? "Now a Markdown note" : "Now a plain text note");
        D.render();
      } catch (e) { fail(e); }
    } });

  // ---------- checklists ----------
  D.editors.checklist = function checklistEditor(page, doc, ctx) {
    const head = editorHead(doc, ctx);
    let items = doc.items;
    let etag = doc.etag;
    const changedKeys = new Set((doc.changedSince && doc.changedSince.items) || []);   // §17.21, for this visit
    const hideKey = "docs.hideTicked";
    const listEl = h("div", { class: "checklist", id: "checklist", role: "list" });
    const count = h("span", { class: "hint", id: "checkCount" });
    const hideBox = h("input", { type: "checkbox", checked: lsGet(hideKey) === "1", onchange: (e) => { lsSet(hideKey, e.target.checked ? "1" : "0"); draw(); } });
    // Find (§1): shows only the items holding the text (Ctrl+F or 🔍)
    const findBox = h("input", { type: "search", id: "checkFind", placeholder: "Find in this list", "aria-label": "Find in this list", maxlength: "200", hidden: true });
    findBox.addEventListener("input", () => draw());
    findBox.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); findBox.value = ""; findBox.hidden = true; draw(); } });
    const openFind = () => { findBox.hidden = false; findBox.focus(); };
    page.onkeydown = (e) => { if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F") && D.state.route === "doc" && document.body.contains(listEl)) { e.preventDefault(); openFind(); } };
    const tools = h("div", { class: "doc-tools" }, count,
      h("label", { class: "mini-toggle" }, hideBox, "Hide ticked"),
      h("button", { class: "btn-ghost btn-small", type: "button", id: "checkFindBtn", title: "Find (Ctrl+F)", onclick: openFind }, "🔍 Find"), findBox,
      doc.canTick ? h("button", { class: "btn-ghost btn-small", type: "button", id: "untickAll", onclick: () => send([{ op: "untickAll" }]) }, "Untick all") : null,
      !doc.canEdit ? h("span", { class: "chip" }, doc.canTick ? "You can tick items" : "Read only") : null);
    const addInput = h("input", { type: "text", id: "addItem", placeholder: "Add an item", maxlength: "2000", "aria-label": "Add an item" });
    const addForm = doc.canEdit ? h("form", { class: "check-add", onsubmit: (e) => {
      e.preventDefault();
      const t = addInput.value.trim();
      if (!t) return;
      addInput.value = "";
      send([{ op: "add", text: t }]).then(() => addInput.focus());
    } }, addInput, h("button", { class: "btn-primary", type: "submit" }, "Add")) : null;
    const secret = secretHint();
    mount(page, head.el, tools, h("div", { class: "editor-card" }, listEl, addForm), secret.el);
    head.status.textContent = "";
    let busy = Promise.resolve();

    function send(ops) {
      busy = busy.then(async () => {
        head.status.textContent = "Saving…";
        try {
          const r = await api(`api/docs/${doc.id}/checklist`, { method: "POST", body: { ops } });
          items = r.items; etag = r.etag; head.status.textContent = "Saved";
        } catch (e) {
          if (e.status === 409 && e.detail && e.detail.items) { items = e.detail.items; etag = e.detail.etag; toast(e.message, true); head.status.textContent = "Saved"; }
          else { head.status.textContent = "Not saved"; fail(e); }
        }
        draw();
      });
      return busy;
    }
    function editText(it, span) {
      const input = h("input", { type: "text", value: it.text, class: "check-edit", maxlength: "2000", "aria-label": "Edit item" });
      let done = false;
      const finish = (keep) => {
        if (done) return; done = true;
        const t = input.value.trim();
        if (keep && t && t !== it.text) send([{ op: "edit", key: it.key, text: t }]); else draw();
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } if (e.key === "Escape") { e.stopPropagation(); finish(false); } });
      input.addEventListener("blur", () => finish(true));
      span.replaceWith(input);
      input.focus();
    }
    // phones: swipe an item sideways to tick or untick it
    function swipeToTick(row, it) {
      if (!doc.canTick) return;
      let x0 = null, y0 = 0, pid = null, moved = false;
      const reset = () => { x0 = null; row.style.transform = ""; row.classList.remove("swipe-ready", "swiping"); };
      row.addEventListener("pointerdown", (e) => {
        if (e.pointerType !== "touch" || e.target.closest("button, input, .check-edit")) return;
        x0 = e.clientX; y0 = e.clientY; pid = e.pointerId; moved = false;
      });
      row.addEventListener("pointermove", (e) => {
        if (x0 === null || e.pointerId !== pid) return;
        const dx = e.clientX - x0, dy = e.clientY - y0;
        if (!moved && Math.abs(dy) > 24 && Math.abs(dy) > Math.abs(dx)) { reset(); return; }
        if (Math.abs(dx) > 10) {
          moved = true;
          row.classList.add("swiping");
          row.style.transform = `translateX(${Math.max(-90, Math.min(90, dx))}px)`;
          row.classList.toggle("swipe-ready", Math.abs(dx) > 64);
        }
      });
      row.addEventListener("pointerup", (e) => {
        if (x0 === null || e.pointerId !== pid) return;
        const dx = e.clientX - x0;
        const go2 = moved && Math.abs(dx) > 64;
        reset();
        if (go2) send([{ op: it.done ? "untick" : "tick", key: it.key }]);
      });
      row.addEventListener("pointercancel", reset);
    }
    function draw() {
      const hide = hideBox.checked;
      const done = items.filter((i) => i.done).length;
      count.textContent = items.length ? `${done} of ${items.length} done` : "";
      const q = findBox.value.trim().toLowerCase();
      const shown = items.filter((i) => !(hide && i.done) && (!q || i.text.toLowerCase().includes(q)));
      mount(listEl, shown.length ? shown.map((it) => {
        const idx = items.indexOf(it);
        const box = h("input", { type: "checkbox", checked: it.done, disabled: !doc.canTick, "aria-label": it.text,
          onchange: () => send([{ op: it.done ? "untick" : "tick", key: it.key }]) });
        const text = h("span", { class: "check-text", title: it.done && it.doneByName ? `Ticked by ${it.doneByName} ${fmtFull(it.doneAt)}` : null }, it.text);
        if (doc.canEdit) { text.classList.add("editable"); text.addEventListener("click", () => editText(it, text)); }
        const who = it.done && it.doneByName ? h("span", { class: "check-who" }, `${it.doneByName} · ${fmtWhen(it.doneAt)}`) : null;
        const tools2 = doc.canEdit ? h("span", { class: "check-tools" },
          h("button", { class: "icon-btn", type: "button", title: "Move up", "aria-label": "Move up", disabled: idx === 0, onclick: () => send([{ op: "move", key: it.key, before: items[idx - 1].key }]) }, "↑"),
          h("button", { class: "icon-btn", type: "button", title: "Move down", "aria-label": "Move down", disabled: idx === items.length - 1,
            onclick: () => send([{ op: "move", key: it.key, before: idx + 2 < items.length ? items[idx + 2].key : null }]) }, "↓"),
          h("button", { class: "icon-btn", type: "button", title: it.level ? "Outdent" : "Indent", "aria-label": it.level ? "Outdent" : "Indent", onclick: () => send([{ op: "indent", key: it.key, level: it.level ? 0 : 1 }]) }, it.level ? "⇤" : "⇥"),
          h("button", { class: "icon-btn danger", type: "button", title: "Delete", "aria-label": "Delete item", onclick: () => send([{ op: "delete", key: it.key }]) }, "✕")) : null;
        const row = h("div", { class: "check-row" + (it.done ? " done" : "") + (it.level ? " indent" : "") + (changedKeys.has(it.key) ? " changed" : ""),
          role: "listitem", dataset: { key: it.key }, title: changedKeys.has(it.key) ? "Changed since you last looked" : null },
          h("label", { class: "check-box" }, box), h("div", { class: "check-main" }, text, who), tools2);
        swipeToTick(row, it);
        return row;
      }) : h("div", { class: "empty" }, q ? "No item holds that text." : items.length ? "Everything is ticked." : (doc.canEdit ? "No items yet — add the first one below." : "No items yet.")));
      secret.check(items.map((i) => i.text).join("\n"));
    }
    draw();
    if (ctx.atLine) {
      const row = listEl.querySelectorAll(".check-row")[ctx.atLine - 1];
      if (row) { row.classList.add("flash"); setTimeout(() => row.scrollIntoView({ block: "center" }), 60); setTimeout(() => row.classList.remove("flash"), 2400); }
    }
    D.state.leaving = async () => { stopPoll(); await busy; };
    poller = setInterval(async () => {
      if (!ctx.current() || D.state.route !== "doc") { stopPoll(); return; }
      if (document.visibilityState !== "visible") return;
      try {
        const e = await api(`api/docs/${doc.id}/etag`);
        setEditing(head.editing, e.editing);
        if (e.etag !== etag) {
          await busy;
          const fresh = await api(`api/docs/${doc.id}`);
          if (fresh.kind !== "checklist") { D.render(); return; }
          if (document.activeElement && document.activeElement.classList.contains("check-edit")) return;
          items = fresh.items; etag = fresh.etag; draw();
        }
      } catch (x) { if (x.status === 404) { stopPoll(); toast("This checklist isn't there any more.", true); } }
    }, POLL_MS);
    if (doc.canEdit && matchMedia("(pointer: fine)").matches && !items.length) addInput.focus();
  };
})();
