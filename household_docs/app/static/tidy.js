"use strict";
/* Household Docs — connect and tidy (SPEC §17.17–§17.19, §17.21): ➕ New → From a template… and ⋯ → Save as
   template…; ⋯ → Filing and clean-up rules… on a folder (and an admin shared folder's top) with the dry run;
   the rules' note on the folder; ✨ Suggest a name and folder for a file (AI on); "Changed since you last
   looked" on an open document, with the changed lines of a note. Everything from the server goes in with
   textContent. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, fmtWhen, titleOf } = D;
  const { mount } = UI;
  const can = (it, role) => ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[it.role] || 0) >= ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[role] || 9);
  const readOnly = () => !!(D.state.me && D.state.me.readOnly);
  const KIND_ICON = { note: "📝", markdown: "📝", checklist: "☑️", sheet: "📊" };

  // =====================================================================
  // Templates (§17.17)
  // =====================================================================
  D.addNew({ id: "template", icon: "🧩", label: "From a template…", order: 84, run: (ctx) => templateDialog(ctx) });

  async function templateDialog(ctx) {
    const body = h("div", { class: "template-box", id: "templates" }, spinner());
    const m = openModal("New from a template", body, { wide: true, focus: false });
    async function draw() {
      let d;
      try { d = await api("api/templates"); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
      const card = (t, removable) => h("div", { class: "template-card", dataset: { template: t.ref } },
        h("button", { class: "template-main", type: "button", onclick: () => use(t) },
          h("span", { class: "tile-icon", "aria-hidden": "true" }, KIND_ICON[t.kind] || "📄"),
          h("span", { class: "row-text" }, h("span", { class: "row-name" }, t.name), t.about ? h("span", { class: "row-meta" }, t.about) : null)),
        removable ? h("button", { class: "icon-btn danger", type: "button", title: "Remove this template", "aria-label": `Remove ${t.name}`, onclick: async () => {
          if (!(await confirmDialog("Remove template", `Remove the template “${t.name}”? Documents made from it stay.`, "Remove", true))) return;
          try { await api(`api/templates/${encodeURIComponent(t.ref)}`, { method: "DELETE" }); draw(); } catch (e) { fail(e); }
        } }, "✕") : null);
      const section = (title, list, removable, empty) => h("section", { class: "template-section" }, h("h4", null, title),
        list.length ? h("div", { class: "template-grid" }, list.map((t) => card(t, removable))) : h("p", { class: "hint" }, empty));
      mount(body,
        section("Built in", d.builtin, false, ""),
        section("The household's", d.household, d.canHousehold, d.canHousehold ? "None yet — ⋯ → Save as template on any note, checklist or sheet." : "None yet — an admin can add them."),
        section("Yours", d.mine, true, "None yet — ⋯ → Save as template on any note, checklist or sheet you can open."),
        h("p", { class: "hint" }, "Templates are plain files in hidden .templates folders. Making a document from one copies it into ", ctx.label, "."));
    }
    async function use(t) {
      const name = await D.askName(`New ${t.name}`, "Name", t.name);
      if (!name) return;
      try {
        const it = await api("api/templates/use", { method: "POST", body: { ref: t.ref, name, parentId: ctx.folderId } });
        m.close();
        toast(`Made in ${ctx.label}`);
        D.openItem(it);
      } catch (e) { fail(e); }
    }
    draw();
  }

  D.addAction({ id: "save-template", icon: "🧩", label: "Save as template…", order: 77,
    show: (it) => ["note", "markdown", "checklist", "sheet"].includes(it.kind) && !it.inTrash && !(it.kind === "sheet" && D.isChild()) && !readOnly(),
    run: async (it) => {
      const name = h("input", { type: "text", id: "templateName", value: titleOf(it), maxlength: "100", "aria-label": "Template name" });
      const house = h("input", { type: "checkbox", id: "templateHousehold" });
      const err = h("div", { class: "error-text", role: "alert" });
      const m = openModal("Save as template", h("div", null,
        h("label", { class: "field" }, "Template name", name),
        D.state.me.isAdmin ? h("label", { class: "mini-toggle" }, house, "For the whole household (everyone sees it)") : null,
        h("p", { class: "hint" }, "A copy of the file as it is now. ➕ New → From a template… makes new documents from it."), err,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "templateSave", onclick: async () => {
            try { await api(`api/docs/${encodeURIComponent(it.id)}/save-template`, { method: "POST", body: { name: name.value.trim(), household: house.checked } }); m.close(); toast("Saved as a template"); }
            catch (e) { err.textContent = e.message; }
          } }, "Save"))));
      setTimeout(() => name.select(), 40);
    } });

  // =====================================================================
  // Filing and clean-up rules (§17.18–§17.19)
  // =====================================================================
  const TYPE_LABEL = { note: "notes", checklist: "checklists", sheet: "sheets", pdf: "PDFs", image: "pictures", file: "other files" };
  function ruleText(r) {
    const cond = [`name like ${r.pattern}`, r.type ? TYPE_LABEL[r.type] : null,
      r.minKb !== null && r.minKb !== undefined ? `at least ${r.minKb} KB` : null, r.maxKb !== null && r.maxKb !== undefined ? `at most ${r.maxKb} KB` : null].filter(Boolean).join(", ");
    const act = [r.rename ? `rename to ${r.rename}` : null, r.moveTo ? `move to ${r.moveToName || "a folder that isn't there any more"}` : null].filter(Boolean).join(", then ");
    return { cond, act };
  }

  D.addAction({ id: "rules", icon: "🗂", label: "Filing and clean-up rules…", order: 78,
    show: (it) => it.kind === "folder" && !it.inTrash && can(it, "editor"), run: (it) => rulesDialog(it.id, it.name) });
  D.rootActions.push({ id: "rules", icon: "🗂", label: "Filing and clean-up rules…", show: (f) => can(f, "editor") && !f.kidsRoot, run: (f) => rulesDialog(f.id, f.name) });

  function folderPicker(ref, onPick) {
    // a folder in the same space (the Move dialog's list)
    const body = h("div", { class: "picker" }, spinner());
    const m = openModal("Move files to…", body, { focus: false });
    async function show(at) {
      mount(body, spinner());
      let d;
      try { d = await api("api/folders" + (at ? `?node=${encodeURIComponent(at)}` : "")); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
      const crumbs = h("div", { class: "crumbs small" }, d.crumbs.map((c, i) => [i ? h("span", { class: "crumb-sep" }, "›") : null,
        (c.id || c.space === "mine") && i < d.crumbs.length - 1 ? h("button", { class: "link-btn", type: "button", onclick: () => show(c.id) }, c.name) : h("span", { class: "crumb current" }, c.name)]));
      mount(body, crumbs, d.folders.length ? h("div", { class: "picker-list" }, d.folders.map((f) => h("button", { class: "picker-row", type: "button", onclick: () => show(f.id) }, "📁 ", f.name, h("span", { class: "picker-go" }, "›")))) : h("div", { class: "empty" }, "No folders here."),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "pickRuleFolder", disabled: !d.canEdit, onclick: () => {
            m.close();
            onPick(d.ref || "root:" + d.rootId, d.crumbs.map((c) => c.name).slice(1).join(" › ") || d.crumbs[d.crumbs.length - 1].name);
          } }, "Move files here")));
    }
    show(ref);
  }

  async function rulesDialog(ref, name) {
    const body = h("div", { class: "rules-box", id: "rulesBox" }, spinner());
    const m = openModal(`Rules for “${name}”`, body, { wide: true, focus: false });
    let d;
    async function load() {
      try { d = await api(`api/rules/${encodeURIComponent(ref)}`); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
      draw();
    }
    function draw(editing) {
      const list = d.filing.map((r, i) => {
        const t = ruleText(r);
        return h("div", { class: "rule-row", dataset: { rule: r.id } },
          h("span", { class: "rule-n" }, String(i + 1)),
          h("div", { class: "rule-text" }, h("div", null, "If ", h("strong", null, t.cond)), h("div", null, "→ ", t.act),
            r.destMissing ? h("div", { class: "hint warn" }, "Its folder isn't there any more — the rule does nothing until you change it.") : null,
            r.by ? h("div", { class: "hint" }, `Acts as ${r.by}`) : null),
          d.canEdit ? h("span", { class: "rule-tools" },
            h("button", { class: "icon-btn", type: "button", title: "Earlier", "aria-label": "Earlier", disabled: i === 0, onclick: () => order(i, -1) }, "↑"),
            h("button", { class: "icon-btn", type: "button", title: "Later", "aria-label": "Later", disabled: i === d.filing.length - 1, onclick: () => order(i, 1) }, "↓"),
            h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => draw(r) }, "Change"),
            h("button", { class: "icon-btn danger", type: "button", title: "Remove", "aria-label": "Remove rule", onclick: async () => {
              try { d = await api(`api/rules/${encodeURIComponent(d.ref)}/filing/${encodeURIComponent(r.id)}`, { method: "DELETE" }); draw(); } catch (e) { fail(e); }
            } }, "✕")) : null);
      });
      mount(body,
        h("section", { class: "rules-section" }, h("h4", null, "🗂 Filing rules"),
          h("p", { class: "hint" }, "When a file arrives here — uploaded, scanned, or added outside the app — the first rule that matches renames it and/or moves it. Each one shows in 🕑 Activity with Undo for 7 days."),
          list.length ? h("div", { class: "rule-list", id: "ruleList" }, list) : h("div", { class: "empty" }, "No filing rules here."),
          editing !== undefined ? ruleForm(editing) : (d.canEdit ? h("div", { class: "actions left" }, h("button", { class: "btn-secondary", type: "button", id: "addRule", onclick: () => draw(null) }, "➕ Add a rule")) : null)),
        cleanupSection(),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => { m.close(); D.render(); } }, "Close")));
    }
    async function order(i, dir) {
      const ids = d.filing.map((r) => r.id);
      const [x] = ids.splice(i, 1);
      ids.splice(i + dir, 0, x);
      try { d = await api(`api/rules/${encodeURIComponent(d.ref)}/filing/order`, { method: "POST", body: { ids } }); draw(); } catch (e) { fail(e); }
    }
    function ruleForm(r) {
      const pat = h("input", { type: "text", id: "rulePattern", maxlength: "200", value: r ? r.pattern : "", placeholder: "Power-bill*.pdf", "aria-label": "Name pattern" });
      const type = h("select", { id: "ruleType", "aria-label": "Type" }, h("option", { value: "" }, "Any type"),
        Object.entries(d.types).map(([k, v]) => h("option", { value: k }, v)));
      type.value = r && r.type ? r.type : "";
      const minKb = h("input", { type: "number", min: "0", id: "ruleMin", placeholder: "KB", value: r && r.minKb !== null && r.minKb !== undefined ? String(r.minKb) : "", "aria-label": "At least (KB)" });
      const maxKb = h("input", { type: "number", min: "0", id: "ruleMax", placeholder: "KB", value: r && r.maxKb !== null && r.maxKb !== undefined ? String(r.maxKb) : "", "aria-label": "At most (KB)" });
      const rename = h("input", { type: "text", id: "ruleRename", maxlength: "200", value: r && r.rename ? r.rename : "", placeholder: "{yyyy}-{mm} Electric.pdf", "aria-label": "Rename to" });
      let moveTo = r ? r.moveTo : null;
      const moveLabel = h("span", { class: "rule-dest", id: "ruleDest" }, r && r.moveTo ? (r.moveToName || "—") : "Stays here");
      const err = h("div", { class: "error-text", role: "alert", id: "ruleError" });
      const preview = h("div", { class: "rule-preview", id: "rulePreview" });
      const val = () => ({ pattern: pat.value.trim(), type: type.value || null, minKb: minKb.value === "" ? null : parseInt(minKb.value, 10),
        maxKb: maxKb.value === "" ? null : parseInt(maxKb.value, 10), rename: rename.value.trim() || null, moveTo: moveTo || null });
      const chips = h("div", { class: "chip-links" }, d.placeholders.map((p) => h("button", { class: "chip", type: "button", title: `Add {${p}}`, onclick: () => {
        const at = rename.selectionStart ?? rename.value.length;
        rename.setRangeText(`{${p}}`, at, rename.selectionEnd ?? at, "end"); rename.focus();
      } }, `{${p}}`)));
      return h("div", { class: "rule-form card", id: "ruleForm" },
        h("label", { class: "field" }, "If a file's name is like", pat, h("span", { class: "hint" }, "* any characters, ? one character, [0-9] one digit — as in search. Case doesn't matter.")),
        h("div", { class: "form-row" }, h("label", { class: "field compact" }, "Type", type), h("label", { class: "field compact" }, "At least (KB)", minKb), h("label", { class: "field compact" }, "At most (KB)", maxKb)),
        h("label", { class: "field" }, "Rename it to (optional)", rename, chips,
          h("span", { class: "hint" }, "{date} 2026-10-04 · {yyyy} {mm} {dd} · {name} its name now · {n} 1, 2, 3 … for the first free number. The extension stays.")),
        h("div", { class: "field" }, "Move it to (optional)", h("div", { class: "form-row" }, moveLabel,
          h("button", { class: "btn-ghost btn-small", type: "button", id: "ruleDestBtn", onclick: () => folderPicker(ref, (to, label) => { moveTo = to; moveLabel.textContent = label; }) }, "Choose…"),
          h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => { moveTo = null; moveLabel.textContent = "Stays here"; } }, "Stay here"))),
        err, preview,
        h("div", { class: "actions" },
          h("button", { class: "btn-ghost", type: "button", onclick: () => draw() }, "Cancel"),
          h("button", { class: "btn-secondary", type: "button", id: "ruleDry", onclick: async () => {
            err.textContent = "";
            try {
              const x = await api(`api/rules/${encodeURIComponent(d.ref)}/filing/dry-run`, { method: "POST", body: val() });
              mount(preview, h("h5", null, x.count ? `It would match ${x.count} file${x.count === 1 ? "" : "s"} already here:` : "No file here matches it now."),
                x.matches.length ? h("ul", { class: "dry-list" }, x.matches.map((y) => h("li", null, y.name, y.error ? h("span", { class: "hint warn" }, ` — ${y.error}`)
                  : [" → ", h("strong", null, y.newName), y.to ? h("span", { class: "hint" }, ` in ${y.to}`) : null]))) : null,
                h("p", { class: "hint" }, x.note));
            } catch (e) { err.textContent = e.message; }
          } }, "Show what would happen"),
          h("button", { class: "btn-primary", type: "button", id: "ruleSave", onclick: async () => {
            err.textContent = "";
            try {
              d = r ? await api(`api/rules/${encodeURIComponent(d.ref)}/filing/${encodeURIComponent(r.id)}`, { method: "PUT", body: val() })
                : await api(`api/rules/${encodeURIComponent(d.ref)}/filing`, { method: "POST", body: val() });
              toast("Rule saved"); draw();
            } catch (e) { err.textContent = e.message; }
          } }, "Save rule")));
    }
    function cleanupSection() {
      const c = d.cleanup;
      const action = h("select", { id: "cleanupAction", "aria-label": "Clean up", disabled: !d.canEdit },
        h("option", { value: "" }, "Never (nothing here is touched)"), h("option", { value: "trash" }, "Move to Trash"), h("option", { value: "archive" }, "Move into an “Archive” folder here"));
      action.value = c ? c.action : "";
      const days = h("input", { type: "number", min: "1", max: "3650", id: "cleanupDays", value: String(c ? c.days : 30), "aria-label": "Days", disabled: !d.canEdit });
      const err = h("div", { class: "error-text", role: "alert" });
      return h("section", { class: "rules-section", id: "cleanupSection" }, h("h4", null, "🧹 Clean-up"),
        h("p", { class: "hint" }, "Once a day: items here that haven't changed for this many days. Folders, favourites and pinned items are left alone. Trash keeps them for the admin-set number of days."),
        h("div", { class: "form-row" }, action, h("span", null, "after"), days, h("span", null, "days"),
          d.canEdit ? h("button", { class: "btn-secondary btn-small", type: "button", id: "cleanupSave", onclick: async () => {
            err.textContent = "";
            try {
              d = action.value ? await api(`api/rules/${encodeURIComponent(d.ref)}/cleanup`, { method: "PUT", body: { action: action.value, days: parseInt(days.value, 10) } })
                : await api(`api/rules/${encodeURIComponent(d.ref)}/cleanup`, { method: "DELETE" });
              toast("Saved"); draw();
            } catch (e) { err.textContent = e.message; }
          } }, "Save") : null),
        c ? h("p", { class: "hint", id: "cleanupText" }, c.text) : null, err);
    }
    load();
  }

  Object.assign(D, { templateDialog, rulesDialog });

  // the folder's rules, on the folder (§17.18–§17.19)
  D.headExtras = D.headExtras || [];
  D.headExtras.push((it, ctx) => {
    if (!it || !(ctx && ctx.folder) || (!it.filingRules && !it.cleanup)) return null;
    return h("div", { class: "head-note rules-note", id: "rulesNote" },
      it.filingRules ? `🗂 ${it.filingRules} filing rule${it.filingRules === 1 ? "" : "s"} for files arriving here` : null,
      it.filingRules && it.cleanup ? " · " : null, it.cleanup ? "🧹 " + it.cleanup : null,
      can(it, "editor") ? [" ", h("button", { class: "link-btn", type: "button", onclick: () => rulesDialog(it.id, it.name) }, "Rules…")] : null);
  });

  // ✨ a name and a folder for a file from its text (AI on, §17.18) — a suggestion, applied only when accepted
  D.filePanelExtras = D.filePanelExtras || [];
  D.filePanelExtras.push((info, modal) => {
    const ai = D.state.me && D.state.me.ai;
    if (!ai || !ai.on || D.state.me.prefs.showAi === false || !can(info, "editor") || info.kind === "folder") return null;
    const box = h("div", { class: "suggest-box", id: "suggestBox" });
    const btn = h("button", { class: "btn-ghost btn-small", type: "button", id: "suggestFiling", title: `Sends its text and your folder names to ${ai.provider}`, onclick: async () => {
      btn.disabled = true;
      mount(box, h("span", { class: "hint" }, `Asking ${ai.provider}…`));
      let s;
      try { s = await api("api/ai/suggest-filing", { method: "POST", body: { id: info.id } }); }
      catch (e) { mount(box, h("div", { class: "error-text" }, e.message)); btn.disabled = false; return; }
      mount(box, h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Suggested"), h("div", { class: "kv-value" },
        s.name ? h("strong", null, s.name) : "(keep the name)", s.folderPath ? h("span", { class: "hint" }, " in " + s.folderPath.replace(/\//g, " › ")) : null)),
      h("div", { class: "actions left" }, h("button", { class: "btn-primary btn-small", type: "button", id: "suggestAccept", onclick: async () => {
        try {
          if (s.folderId && s.folderId !== info.parentId) await api(`api/nodes/${encodeURIComponent(info.id)}/move`, { method: "POST", body: { parentId: s.folderId } });
          if (s.name && s.name !== info.name) await api(`api/nodes/${encodeURIComponent(info.id)}/rename`, { method: "POST", body: { name: s.name } });
          toast("Done"); if (modal) modal.close(); D.render();
        } catch (e) { fail(e); }
      } }, "Accept"), h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => { mount(box); btn.disabled = false; } }, "No thanks")));
    } }, "✨ Suggest a name and folder");
    return h("div", { class: "suggest" }, btn, box);
  });

  // =====================================================================
  // Changed since you last looked (§17.21) — on an open document
  // =====================================================================
  D.headExtras.push((doc, ctx) => {
    const c = doc && doc.changedSince;
    if (!c || (ctx && ctx.folder)) return null;
    const who = c.by && c.by.length ? ` by ${c.by.join(", ")}` : "";
    const when = ` since you last looked (${fmtWhen(c.since)})`;
    const box = h("div", { class: "changed-note", id: "changedNote", role: "status" });
    const parts = [h("span", null, h("span", { class: "new-dot", "aria-hidden": "true" }), ` Changed${who}${when}.`)];
    if (c.before !== undefined && doc.text !== undefined) {
      const diffBox = h("div", { class: "diff changed-diff", id: "changedDiff", hidden: true });
      parts.push(h("span", null, " "), h("button", { class: "link-btn", type: "button", id: "showChanges", onclick: (e) => {
        diffBox.hidden = !diffBox.hidden;
        e.target.textContent = diffBox.hidden ? "Show what changed" : "Hide";
        if (!diffBox.hidden && !diffBox.firstChild) {
          const d = DocsText.lineDiff(c.before, doc.text);
          if (!d) { mount(diffBox, h("div", { class: "hint" }, "Too long to compare here.")); return; }
          let line = 0;
          mount(diffBox, d.some(([k]) => k !== " ")
            ? d.map(([k, text]) => {
              if (k !== "-") line += 1;
              return h("div", { class: "diff-line " + (k === "-" ? "theirs" : k === "+" ? "mine" : "same"), title: k === "+" ? `Line ${line}: new or changed` : k === "-" ? "Removed" : null },
                (k === "-" ? "− " : k === "+" ? "+ " : "  ") + text);
            }) : h("div", { class: "hint" }, "Only spacing changed."));
        }
      } }, "Show what changed"), diffBox);
    } else if (c.items) {
      parts.push(h("span", null, ` ${c.items.length} item${c.items.length === 1 ? " is" : "s are"} new or changed (highlighted).`));
    } else if (c.cells) {
      const n = Object.values(c.cells).reduce((a, x) => a + x.length, 0);
      parts.push(h("span", null, ` ${n} cell${n === 1 ? "" : "s"} changed (highlighted).`));
    }
    mount(box, parts);
    return box;
  });

})();
