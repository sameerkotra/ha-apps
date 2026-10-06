"use strict";
/* Household Docs — files people bring in and take out, and working on several items at once (SPEC §9.2, §13):
   - uploads (⬆ Upload, ➕ New → Upload files, or files dropped from the computer onto a folder), one file per
     request with a progress panel; names already in the folder ask once: Replace / Keep both / Skip;
   - the file panel: a preview for PNG, JPEG, GIF and WebP (the server checks the bytes), details, Download,
     Replace with a new copy;
   - the selection bar (tick boxes in folders and search results): Move, Copy to My docs, Share, Download (.zip),
     Favourite, Delete — and whatever later steps add with Docs.addBulk (tags);
   - drag and drop on a computer: items onto a folder (or a breadcrumb) to move them;
   - the 📁 Shared folders space (#/folders): the admin shared folders you can use. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, pageHead, fmtSize, fmtWhen, fmtFull, go } = D;
  const { mount } = UI;
  const MB = 1024 * 1024;
  const roleRank = { viewer: 1, editor: 2, manager: 3, owner: 4 };
  const can = (it, role) => (roleRank[it.role] || 0) >= roleRank[role];

  // =====================================================================
  // uploads
  // =====================================================================
  let panel = null;
  function uploadPanel() {
    if (panel && document.body.contains(panel.el)) return panel;
    const list = h("div", { class: "upload-list" });
    const title = h("strong", null, "Uploading");
    const el = h("div", { class: "upload-panel", role: "status", "aria-live": "polite", id: "uploadPanel" },
      h("div", { class: "upload-head" }, title, h("button", { class: "icon-btn", type: "button", "aria-label": "Close", onclick: () => { el.remove(); panel = null; } }, "✕")), list);
    document.body.appendChild(el);
    panel = { el, list, title };
    return panel;
  }
  function sendFile(file, ref, clash, onProgress) {
    return new Promise((resolve, reject) => {
      const x = new XMLHttpRequest();
      x.open("POST", `api/nodes/${encodeURIComponent(ref || "mine")}/upload?name=${encodeURIComponent(file.name)}&onClash=${clash}`);
      x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      x.onload = () => {
        let body = null;
        try { body = JSON.parse(x.responseText); } catch (e) { /* not JSON */ }
        if (x.status >= 200 && x.status < 300) { resolve(body); return; }
        const d = body && body.detail;
        reject(new Error(typeof d === "string" ? d : (d && d.message) || `The upload failed (${x.status}).`));
      };
      x.onerror = () => reject(new Error("Can't reach Household Docs."));
      x.send(file);
    });
  }
  function askClash(names) {
    return new Promise((resolve) => {
      let done = false;
      const finish = (v) => { if (!done) { done = true; m.close(); resolve(v); } };
      const m = openModal(names.length === 1 ? "Already there" : `${names.length} names already there`, h("div", null,
        h("p", null, names.length === 1 ? `“${names[0]}” is already in this folder.` : "These are already in this folder: " + names.slice(0, 8).join(", ") + (names.length > 8 ? " …" : "")),
        h("p", { class: "hint" }, "Replace keeps the previous copy (History for documents, 7 days for other files)."),
        h("div", { class: "actions" },
          h("button", { class: "btn-ghost", type: "button", id: "clashSkip", onclick: () => finish("skip") }, "Skip"),
          h("button", { class: "btn-secondary", type: "button", id: "clashKeep", onclick: () => finish("keep") }, "Keep both"),
          h("button", { class: "btn-primary", type: "button", id: "clashReplace", onclick: () => finish("replace") }, "Replace"))),
      { focus: false, onClose: () => { if (!done) { done = true; resolve(null); } } });
    });
  }
  async function uploadFiles(fileList, folder, opts = {}) {
    const files = Array.from(fileList || []).filter((f) => f && f.name);
    if (!files.length) return [];
    const limitMb = (D.state.me && D.state.me.app.uploadMb) || 100;
    const tooBig = files.filter((f) => f.size > limitMb * MB);
    if (tooBig.length) toast(`${tooBig.map((f) => f.name).join(", ")}: bigger than the ${limitMb} MB upload limit.`, true);
    let todo = files.filter((f) => f.size <= limitMb * MB);
    let clash = opts.clash || "keep";
    if (!opts.clash && folder && folder.names) {
      const taken = DocsText.clashes(folder.names, todo.map((f) => f.name));
      if (taken.length) {
        const choice = await askClash(taken);
        if (!choice) return [];
        if (choice === "skip") todo = todo.filter((f) => !taken.includes(f.name));
        else clash = choice;
      }
    }
    if (!todo.length) return [];
    const p = uploadPanel();
    p.el.hidden = false;
    p.title.textContent = `Uploading to ${folder ? folder.name : "My docs"}`;
    const made = [];
    let failed = 0;
    for (const f of todo) {
      const bar = h("progress", { max: "1", value: "0" });
      const state = h("span", { class: "hint" }, fmtSize(f.size));
      const row = h("div", { class: "upload-row", dataset: { file: f.name } }, h("span", { class: "upload-name" }, f.name), bar, state);
      p.list.appendChild(row);
      try {
        const it = await sendFile(f, folder ? folder.id : null, clash, (v) => { bar.value = v; });
        bar.value = 1;
        state.textContent = "✓";
        row.classList.add("done");
        made.push(it);
      } catch (e) {
        failed += 1;
        state.textContent = e.message;
        row.classList.add("failed");
      }
    }
    p.title.textContent = failed ? `${made.length} uploaded, ${failed} not` : `${made.length} uploaded`;
    if (!failed) setTimeout(() => { if (panel === p) { p.el.remove(); panel = null; } }, 4000);
    if (made.length && (D.state.route === "folder" || D.state.route === "mine")) D.render();
    return made;
  }
  function pickUpload(folder, opts = {}) {
    const input = h("input", { type: "file", multiple: !opts.single, hidden: true, id: "uploadInput" });
    input.addEventListener("change", () => { const fl = Array.from(input.files); input.remove(); uploadFiles(fl, folder, opts); });
    document.body.appendChild(input);
    input.click();
  }
  Object.assign(D, { uploadFiles, pickUpload });
  D.addNew({ id: "upload", icon: "⬆", label: "Upload files", order: 80, run: (ctx) => pickUpload({ id: ctx.folderId, name: ctx.label, names: ctx.names }) });

  // =====================================================================
  // drag and drop (a computer with a mouse)
  // =====================================================================
  const hasItems = (e) => Array.from(e.dataTransfer.types || []).includes("application/x-docs-ids");
  const hasFiles = (e) => Array.from(e.dataTransfer.types || []).includes("Files");
  async function moveIds(ids, folder) {
    let moved = 0;
    const errors = [];
    for (const id of ids) {
      if (id === folder.id) continue;
      try { await api(`api/nodes/${encodeURIComponent(id)}/move`, { method: "POST", body: { parentId: folder.id } }); moved += 1; }
      catch (e) { errors.push(e.message); }
    }
    if (errors.length) toast(errors[0], true);
    if (moved) { toast(`Moved ${moved === 1 ? "1 item" : moved + " items"} to ${folder.name}`); D.render(); }
  }
  // a folder row, tile or breadcrumb: items dragged onto it move there; files from the computer upload there
  function dropTarget(el, folder) {
    el.addEventListener("dragover", (e) => {
      if (!hasItems(e) && !hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = hasItems(e) ? "move" : "copy";
      el.classList.add("drop-over");
    });
    el.addEventListener("dragleave", () => el.classList.remove("drop-over"));
    el.addEventListener("drop", (e) => {
      el.classList.remove("drop-over");
      if (!hasItems(e) && !hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      if (hasItems(e)) {
        let ids = [];
        try { ids = JSON.parse(e.dataTransfer.getData("application/x-docs-ids")); } catch (x) { ids = []; }
        moveIds(ids, folder);
      } else uploadFiles(e.dataTransfer.files, folder);
    });
  }
  // the folder view's list: files from the computer upload into the folder you're looking at
  function dropZone(el, folder) {
    el.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = "copy"; el.classList.add("drop-over"); });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop-over"); });
    el.addEventListener("drop", (e) => {
      el.classList.remove("drop-over");
      if (!hasFiles(e)) return;
      e.preventDefault();
      uploadFiles(e.dataTransfer.files, folder);
    });
  }
  Object.assign(D, { dropTarget, dropZone });

  // =====================================================================
  // the file panel (preview, details, download, replace)
  // =====================================================================
  async function filePanel(it) {
    const body = h("div", { class: "file-panel" }, spinner());
    const m = openModal(it.name, body, { wide: true, focus: false });
    let info;
    try { info = await api(`api/nodes/${encodeURIComponent(it.id)}`); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
    const kv = (k, v) => h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, k), h("div", { class: "kv-value" }, v));
    const image = info.preview;                 // the server checked the first bytes: a real PNG, JPEG, GIF or WebP
    const preview = image ? h("div", { class: "preview-box" }, h("img", { class: "preview-img", id: "previewImg", alt: info.name,
      src: `api/nodes/${encodeURIComponent(info.id)}/preview`, onerror: (e) => e.target.replaceWith(h("div", { class: "empty" }, "No preview — it isn't a picture the app can show. Download it instead.")) }))
      : h("div", { class: "preview-box none" }, h("span", { class: "tile-icon", "aria-hidden": "true" }, D.iconOf(info)),
        h("p", { class: "hint" }, info.ext === "pdf" ? "PDFs open in your own viewer after downloading."
          : D.IMAGE_EXTS.includes(info.ext) ? "No preview — this isn't a picture the app can show (its content doesn't match its name). Download it instead." : "This kind of file downloads to open."));
    const where = info.rootKind === "shared" ? info.rootLabel : (info.ownerId === D.state.me.id ? "My docs" : `${info.ownerName}'s folder`);
    mount(body, preview,
      (D.filePanelExtras || []).map((fn) => fn(info, m)),
      h("div", { class: "kv" }, kv("Size", fmtSize(info.size)), kv("Changed", [fmtFull(info.modified), info.updatedByName ? ` by ${info.updatedByName}` : (info.outside ? " outside the app" : "")]),
        kv("In", [where, ...info.crumbs.slice(info.rootKind === "shared" ? 2 : 1, -1).map((c) => " › " + c.name)]), kv("Stored at", h("code", null, info.path))),
      h("div", { class: "actions" },
        can(info, "editor") ? h("button", { class: "btn-ghost", type: "button", id: "replaceFile", onclick: () => { m.close(); replaceFile(info); } }, "Replace with a new copy…") : null,
        h("button", { class: "btn-ghost", type: "button", onclick: (e) => D.itemMenu(e.currentTarget, info, { inEditor: true }) }, "More…"),
        h("a", { class: "btn-primary", href: `api/nodes/${encodeURIComponent(info.id)}/file`, download: info.name, id: "downloadFile" }, "Download")));
  }
  function replaceFile(it) {
    const input = h("input", { type: "file", hidden: true });
    input.addEventListener("change", async () => {
      const f = input.files[0];
      input.remove();
      if (!f) return;
      const named = new File([f], it.name, { type: f.type });
      const done = await uploadFiles([named], { id: it.parentRef, name: it.rootLabel || "the folder", names: null }, { clash: "replace" });
      if (done.length) toast(`${it.name} replaced — the previous copy is under Earlier copies for 7 days`);
    });
    document.body.appendChild(input);
    input.click();
  }
  D.filePanel = filePanel;
  D.addAction({ id: "replace", label: "Replace with a new copy…", icon: "⬆", order: 72, show: (it) => it.kind === "file" && can(it, "editor"), run: (it) => replaceFile(it) });

  // =====================================================================
  // several items at once: the selection and its bar
  // =====================================================================
  function makeSelection(items, redraw) {
    const sel = {
      ids: new Set(), items: new Map(),
      toggle(it, on) { if (on) { sel.ids.add(it.id); sel.items.set(it.id, it); } else { sel.ids.delete(it.id); sel.items.delete(it.id); } redraw(); },
      all() { items.forEach((it) => { sel.ids.add(it.id); sel.items.set(it.id, it); }); redraw(); },
      clear() { sel.ids.clear(); sel.items.clear(); redraw(); },
      list() { return Array.from(sel.items.values()); },
      count: () => sel.ids.size, total: () => items.length,
    };
    return sel;
  }
  async function eachItem(list, fn, doneMsg) {
    let ok = 0;
    const errors = [];
    for (const it of list) {
      try { await fn(it); ok += 1; } catch (e) { errors.push(`${it.name}: ${e.message}`); }
    }
    if (errors.length) toast(errors.slice(0, 2).join(" · ") + (errors.length > 2 ? ` (+${errors.length - 2})` : ""), true);
    if (ok) toast(doneMsg(ok));
    return ok;
  }
  D.addBulk({ id: "move", label: "Move…", icon: "📂", order: 10, show: (l) => l.every((it) => can(it, "editor")) && new Set(l.map((it) => it.rootId)).size === 1,
    run: (l, sel) => D.moveDialog(l, () => { sel.clear(); D.render(); }) });
  D.addBulk({ id: "copy", label: "Copy to My docs", icon: "⧉", order: 20, run: async (l, sel) => {
    await eachItem(l, (it) => api(`api/nodes/${it.id}/copy`, { method: "POST", body: {} }), (n) => `Copied ${n} to My docs`);
    sel.clear();
  } });
  D.addBulk({ id: "share", label: "Share…", icon: "👥", order: 30, show: (l) => l.every((it) => can(it, "manager") && it.ownerId), run: (l, sel) => bulkShare(l, sel) });
  D.addBulk({ id: "download", label: "Download (.zip)", icon: "⬇", order: 40, run: (l) => {
    const a = h("a", { href: "api/zip?ids=" + l.map((it) => encodeURIComponent(it.id)).join(","), download: "" });
    document.body.appendChild(a); a.click(); a.remove();
  } });
  D.addBulk({ id: "favourite", label: "Favourite", icon: "⭐", order: 50, run: async (l, sel) => {
    await eachItem(l, (it) => api(`api/nodes/${it.id}/favourite`, { method: "POST", body: { value: true } }), (n) => `${n} added to Favourites`);
    sel.clear(); D.render();
  } });
  D.addBulk({ id: "delete", label: "Delete", icon: "🗑", order: 90, danger: true, show: (l) => l.every((it) => can(it, "editor") && !it.inTrash), run: async (l, sel) => {
    if (!(await confirmDialog("Delete", `Delete ${l.length} item${l.length === 1 ? "" : "s"}? They go to Trash, where they can be restored.`, "Delete", true))) return;
    await eachItem(l, (it) => api(`api/nodes/${it.id}`, { method: "DELETE" }), (n) => `${n} moved to Trash`);
    sel.clear(); D.render();
  } });

  async function bulkShare(list, sel) {
    let ppl = [];
    try { ppl = (await D.people()).filter((p) => !p.you); } catch (e) { fail(e); return; }
    const me = D.state.me;
    const who = h("select", { "aria-label": "Share with", id: "bulkShareWho" }, h("option", { value: "" }, "Choose a person…"),
      me.app.everyoneShares ? h("option", { value: "*" }, "👪 Everyone") : null, ppl.map((p) => h("option", { value: p.id }, p.name)));
    const role = h("select", { "aria-label": "Role", id: "bulkShareRole" }, h("option", { value: "viewer" }, "Can view"), h("option", { value: "editor" }, "Can edit"));
    who.addEventListener("change", () => { if (who.value === "*") role.value = me.app.everyoneDefaultRole; });
    const err = h("div", { class: "error-text", role: "alert" });
    const m = openModal(`Share ${list.length} item${list.length === 1 ? "" : "s"}`, h("div", null,
      h("div", { class: "share-add" }, who, role), err,
      h("p", { class: "hint" }, "Each item gets the same share; open Share… on one item to fine-tune it."),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "bulkShareGo", onclick: async () => {
          if (!who.value) { err.textContent = "Choose who to share with."; return; }
          m.close();
          await eachItem(list, (it) => api(`api/nodes/${it.id}/shares`, { method: "POST", body: { userId: who.value, role: role.value } }), (n) => `Shared ${n}`);
          sel.clear(); D.render();
        } }, "Share"))));
  }

  function selectionBar(sel, ctx = {}) {
    const n = sel.count();
    if (!n) return null;
    const list = sel.list();
    const acts = D.bulk.filter((b) => !b.show || b.show(list, ctx)).map((b) => h("button", { class: "btn-ghost btn-small" + (b.danger ? " danger" : ""), type: "button",
      dataset: { bulk: b.id }, onclick: () => b.run(list, sel, ctx) }, b.icon ? b.icon + " " : "", b.label));
    return h("div", { class: "select-bar", role: "toolbar", "aria-label": "Selected items", id: "selectBar" },
      h("strong", null, `${n} selected`),
      n < sel.total() ? h("button", { class: "link-btn", type: "button", onclick: () => sel.all() }, "Select all") : null,
      h("span", { class: "select-acts" }, acts),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Clear the selection", title: "Clear", onclick: () => sel.clear() }, "✕"));
  }
  Object.assign(D, { makeSelection, selectionBar });

  // =====================================================================
  // 📁 Shared folders (#/folders)
  // =====================================================================
  D.route("folders", async (page, _a, current) => {
    mount(page, pageHead("Shared folders"), spinner());
    const [me, d] = await Promise.all([D.refreshMe(), api("api/shared-folders")]);
    if (!current()) return;
    const rows = d.folders.map((f) => f.exists
      ? h("a", { class: "item-row", href: D.folderHash("root:" + f.rootId), dataset: { root: f.rootId } },
        h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, f.mode === "rw" ? "📂" : "🗂️"),
          h("span", { class: "row-text" }, h("span", { class: "row-name" }, f.label,
            h("span", { class: "chip mode-chip role-" + (f.mode === "rw" ? "editor" : "viewer") }, f.mode === "rw" ? "✏️ Read and write" : "🔒 Read only")))))
      : h("div", { class: "item-row", dataset: { root: f.rootId } }, h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, "⚠"),
        h("span", { class: "row-text" }, h("span", { class: "row-name" }, f.label), h("span", { class: "row-meta" }, "Not found — is its storage connected? (Only admins see this.)")))));
    mount(page, pageHead("Shared folders"),
      h("p", { class: "hint" }, "Folders in Home Assistant's /share that an admin opened up for you — 🔒 read only, or ✏️ read and write. Everyone with access sees the same files."),
      h("div", { class: "card list-card" }, rows.length ? h("div", { class: "item-list" }, rows)
        : h("div", { class: "empty" }, "No shared folders for you yet." + (me.isAdmin ? " Add one on Admin → Shared folders." : ""))));
  });
})();
