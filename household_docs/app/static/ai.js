"use strict";
/* Household Docs — optional AI (SPEC §17.6). Shown only when an admin has turned AI on and set it up, and the
   person hasn't hidden the AI buttons (Settings → You). Every action asks first in a dialog that names the provider,
   the model and what exactly is sent; nothing is sent before that. Read text (pictures, scanned PDFs; the per-folder
   switch), Summarise (a panel; Save as note), Checklist from text, Sheet from a table (text or a photo), Ask about a
   folder (the answer links the files it used), Admin → AI usage and App settings → AI's Test connection. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, spinner, pageHead, fmtWhen } = D;
  const { mount } = UI;
  const can = (it, role) => ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[it.role] || 0) >= ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[role] || 9);
  const aiState = () => (D.state.me && D.state.me.ai) || {};
  const aiOn = () => !!aiState().on && D.state.me.prefs.showAi !== false && !(D.state.me && D.state.me.readOnly);
  const TEXT_KINDS = ["note", "markdown", "checklist"];
  const isPdf = (it) => (it.ext || "").toLowerCase() === "pdf";
  const readable = (it) => it.kind === "file" && (it.preview || (isPdf(it) && (it.pdfText === "scanned" || it.pdfText === undefined)));

  // ---------- the dialog that names the provider before anything is sent ----------
  function ask(title, sends, opts = {}) {
    const a = aiState();
    return new Promise((resolve) => {
      let done = false;
      const ok = h("button", { class: "btn-primary", type: "button", id: "aiSend" }, opts.okLabel || "Send");
      const body = h("div", { class: "ai-confirm" },
        h("p", null, "This sends ", h("strong", null, sends), " to ", h("strong", { id: "aiProvider" }, a.provider || "the AI provider"),
          ` (model ${opts.vision ? a.visionModel : a.model}${a.address ? `, at ${a.address}` : ""}).`),
        h("p", { class: "hint" }, "Nothing else is sent. The answer is shown to you first; the provider may keep what it receives under its own terms."),
        opts.extra || null,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), ok));
      const m = openModal(title, body, { onClose: () => { if (!done) resolve(false); } });
      ok.addEventListener("click", () => { done = true; m.close(); resolve(true); });
    });
  }
  function working(title) {
    const body = h("div", { class: "ai-working" }, spinner(), h("p", { class: "hint" }, "Waiting for the AI model — this can take a little while."));
    return openModal(title, body, { focus: false });
  }

  // ---------- Read text ----------
  async function readText(it, after) {
    if (!(await ask("Read text with AI", isPdf(it) ? `the scanned pages of “${it.name}” (as pictures)` : `the picture “${it.name}”`, { vision: true, okLabel: "Read text" }))) return;
    const w = working("Reading text…");
    try {
      await api("api/ai/ocr", { method: "POST", body: { id: it.id } });
      w.close();
      toast("Text read — search finds it now");
      if (after) after(); else if (D.filePanel) D.filePanel(it);
    } catch (e) { w.close(); fail(e); }
  }
  D.aiOffer = (saved) => {
    if (!aiOn() || !saved.length) return;
    const btn = h("button", { class: "link-btn", type: "button", id: "aiReadScan", onclick: async () => {
      for (const s of saved) await readText(s, () => {});
    } }, "✨ Read text");
    toast("", false, { extra: h("span", null, "Saved. ", btn), ms: 9000 });
  };
  D.aiTextBlock = (info, t, reload) => {
    const parts = [];
    if (t.ai) {
      const ta = h("textarea", { class: "ai-text", id: "aiText", rows: "6", readonly: !t.canEdit, "aria-label": "Text read by AI" });
      ta.value = t.ai.text;
      const saveBtn = h("button", { class: "btn-secondary btn-small", type: "button", id: "aiTextSave", hidden: true, onclick: async () => {
        try { await api(`api/nodes/${encodeURIComponent(info.id)}/text`, { method: "PUT", body: { text: ta.value } }); toast("Saved — search uses it"); reload(); } catch (e) { fail(e); }
      } }, "Save");
      ta.addEventListener("input", () => { saveBtn.hidden = false; });
      parts.push(h("div", { class: "ai-text-box" }, h("div", { class: "ai-text-head" }, h("strong", null, "Text read by AI"),
        h("span", { class: "hint" }, [t.ai.model, t.ai.editedByName ? `corrected by ${t.ai.editedByName}` : null, fmtWhen(t.ai.updatedAt), t.ai.stale ? "the file changed since" : null].filter(Boolean).join(" · "))),
        ta, h("div", { class: "hint" }, "Kept in the app's database (searchable), never written into the file.", t.canEdit ? " You can correct it." : ""), saveBtn));
    }
    if (aiOn() && t.canEdit && (info.preview || (t.pdf && t.pdf.status === "scanned"))) {
      parts.push(h("div", { class: "actions" }, h("button", { class: "btn-secondary btn-small", type: "button", id: "aiReadText", onclick: () => readText(info, reload) },
        t.ai ? "✨ Read text again" : "✨ Read text with AI")));
    }
    return parts;
  };
  D.addAction({ id: "ai-ocr", order: 79, icon: "✨", label: "Read text with AI", show: (it) => aiOn() && readable(it) && can(it, "editor") && !it.inTrash, run: (it) => readText(it) });

  // ---------- Summarise ----------
  async function summarise(it) {
    if (!(await ask("Summarise with AI", `the text of “${it.name}”`, { okLabel: "Summarise" }))) return;
    const w = working("Summarising…");
    let r;
    try { r = await api("api/ai/summarise", { method: "POST", body: { id: it.id } }); } catch (e) { w.close(); fail(e); return; }
    w.close();
    const out = h("div", { class: "md-body ai-summary", id: "aiSummary" });
    if (window.DocsMd) out.appendChild(DocsMd.render(DocsMd.parse(r.summary), document, { docLink: () => ({ state: "missing" }) }));
    else out.textContent = r.summary;
    const title = D.titleOf(it) + " — summary";
    const m = openModal("Summary", h("div", null, h("p", { class: "hint" }, `Of “${it.name}”, by ${aiState().provider}. Check it against the document.`), out,
      h("div", { class: "actions" },
        h("button", { class: "btn-ghost", type: "button", onclick: () => { if (navigator.clipboard) navigator.clipboard.writeText(r.summary).then(() => toast("Copied")); } }, "Copy"),
        h("button", { class: "btn-primary", type: "button", id: "aiSaveNote", onclick: async () => {
          const parent = can(it, "editor") ? it.parentRef || null : null;     // beside it, else My docs
          try {
            const made = await api("api/docs", { method: "POST", body: { kind: "markdown", name: title, parentId: parent, text: r.summary + "\n" } });
            m.close(); toast(`Saved as ${made.name}`); D.openItem(made);
          } catch (e) { fail(e); }
        } }, "Save as note"))), { wide: true });
  }
  D.addAction({ id: "ai-summary", order: 79.5, icon: "✨", label: "Summarise with AI", run: (it) => summarise(it),
    show: (it) => aiOn() && !it.inTrash && (TEXT_KINDS.includes(it.kind) || (isPdf(it) && (it.pdfText === "ok" || it.aiText)) || (it.kind === "file" && it.aiText)) });

  // ---------- Checklist from text ----------
  function itemsEditor(items) {
    const ta = h("textarea", { class: "ai-items", id: "aiItems", rows: String(Math.min(14, items.length + 1)), "aria-label": "Items, one per line" });
    ta.value = items.join("\n");
    return ta;
  }
  async function checklistFrom(ctx, it) {
    let text = "";
    if (!it) {
      const ta = h("textarea", { rows: "8", id: "aiSource", "aria-label": "Text", placeholder: "Paste a recipe, an email, a list of things to do…" });
      const go_ = await new Promise((resolve) => {
        let done = false;
        const m = openModal("Checklist from text", h("div", null, h("p", { class: "hint" }, "Paste the text; the AI model turns it into checklist items you can check before the checklist is made."), ta,
          h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
            h("button", { class: "btn-primary", type: "button", id: "aiNext", onclick: () => { done = true; m.close(); resolve(true); } }, "Next"))), { onClose: () => { if (!done) resolve(false); } });
      });
      text = ta.value.trim();
      if (!go_ || !text) return;
    }
    if (!(await ask("Checklist from text", it ? `the text of “${it.name}”` : `the text you pasted (${text.length} characters)`, { okLabel: "Make items" }))) return;
    const w = working("Making the items…");
    let r;
    try { r = await api("api/ai/to-checklist", { method: "POST", body: it ? { id: it.id } : { text } }); } catch (e) { w.close(); fail(e); return; }
    w.close();
    const ed = itemsEditor(r.items);
    const name = h("input", { type: "text", id: "aiListName", maxlength: "200", value: it ? D.titleOf(it) + " — checklist" : "Checklist", "aria-label": "Name" });
    const m = openModal("New checklist", h("div", null, h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "Items (one per line — change anything)", ed),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "aiMakeList", onclick: async () => {
          const lines = ed.value.split("\n").map((x) => x.trim()).filter(Boolean);
          try {
            const made = await api("api/docs", { method: "POST", body: { kind: "checklist", name: name.value.trim() || "Checklist", parentId: ctx ? ctx.folderId : null } });
            if (lines.length) await api(`api/docs/${encodeURIComponent(made.id)}/checklist`, { method: "POST", body: { ops: lines.map((t) => ({ op: "add", text: t })) } });
            m.close(); toast(`${made.name} made`); D.openItem(made);
          } catch (e) { fail(e); }
        } }, "Make the checklist"))), { wide: true });
  }
  D.addNew({ id: "ai-checklist", icon: "✨", label: "Checklist from text (AI)", order: 25, show: () => aiOn(), run: (ctx) => checklistFrom(ctx, null) });
  D.addAction({ id: "ai-checklist", order: 79.6, icon: "✨", label: "Make a checklist from this (AI)", show: (it) => aiOn() && ["note", "markdown"].includes(it.kind) && !it.inTrash,
    run: (it) => checklistFrom({ folderId: can(it, "editor") ? it.parentRef : null }, it) });

  // ---------- Sheet from a table ----------
  function photoB64(file) {
    // a photo, made smaller in the browser (≤ 1600 px, JPEG) before it is sent
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * s); c.height = Math.round(img.naturalHeight * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", 0.85));
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That isn't a picture this browser can open.")); };
      img.src = url;
    });
  }
  function csvOf(rows) {
    const cell = (v) => { const s = typeof v === "number" ? String(v) : String(v ?? ""); return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    return rows.map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
  }
  async function sheetFrom(ctx) {
    const ta = h("textarea", { rows: "7", id: "aiTable", "aria-label": "Table", placeholder: "Paste a table (from an email, a web page…)" });
    const file = h("input", { type: "file", accept: "image/*", id: "aiTablePhoto", "aria-label": "Or a photo of a table" });
    const go_ = await new Promise((resolve) => {
      let done = false;
      const m = openModal("Sheet from a table", h("div", null, h("p", { class: "hint" }, "Paste the table, or choose a photo of one; the AI model reads it into rows you check before the sheet is made."),
        ta, h("label", { class: "field" }, "…or a photo", file),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "aiNext", onclick: () => { done = true; m.close(); resolve(true); } }, "Next"))), { onClose: () => { if (!done) resolve(false); } });
    });
    if (!go_) return;
    const photo = file.files[0];
    const text = ta.value.trim();
    if (!photo && !text) return;
    if (!(await ask("Sheet from a table", photo ? `the photo “${photo.name}” (made smaller first)` : `the table you pasted (${text.length} characters)`, { vision: !!photo, okLabel: "Read the table" }))) return;
    const w = working("Reading the table…");
    let r;
    try {
      const body = photo ? { image: await photoB64(photo) } : { text };
      r = await api("api/ai/to-sheet", { method: "POST", body });
    } catch (e) { w.close(); fail(e); return; }
    w.close();
    const table = h("table", { class: "ai-table", id: "aiRows" }, r.rows.slice(0, 50).map((row, i) => h("tr", null, row.map((v) => h(i ? "td" : "th", null, String(v))))));
    const name = h("input", { type: "text", id: "aiSheetName", maxlength: "200", value: "Table", "aria-label": "Name" });
    const m = openModal("New sheet", h("div", null, h("label", { class: "field" }, "Name", name),
      h("p", { class: "hint" }, `${r.rows.length} row${r.rows.length === 1 ? "" : "s"}${r.rows.length > 50 ? " (the first 50 shown)" : ""} — check them; you can fix anything in the sheet afterwards.`),
      h("div", { class: "table-scroll" }, table),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "aiMakeSheet", onclick: async () => {
          try {
            const fmt = (D.state.me.prefs.newSheets || "xlsx");
            const q = `name=${encodeURIComponent((name.value.trim() || "Table") + ".csv")}&format=${fmt}` + (ctx.folderId ? `&parentId=${encodeURIComponent(ctx.folderId)}` : "");
            const made = await api("api/import?" + q, { method: "POST", rawBody: new Blob([csvOf(r.rows)], { type: "text/csv" }), headers: { "Content-Type": "text/csv" } });
            m.close(); toast("Sheet made"); D.go("#/doc/" + encodeURIComponent(made.id || (made.item && made.item.id)));
          } catch (e) { fail(e); }
        } }, "Make the sheet"))), { wide: true });
  }
  D.addNew({ id: "ai-sheet", icon: "✨", label: "Sheet from a table (AI)", order: 35, show: () => aiOn(), run: (ctx) => sheetFrom(ctx) });

  // ---------- Ask about a folder ----------
  async function askFolder(ref, name) {
    const q = h("textarea", { rows: "3", id: "aiQuestion", maxlength: "2000", "aria-label": "Your question", placeholder: "When was the boiler last serviced?" });
    const ok = await new Promise((resolve) => {
      let done = false;
      const m = openModal(`Ask about ${name}`, h("div", null, q, h("p", { class: "hint" }, "Up to 30 files in this folder (and the folders in it) that you can open, as text — the newest first."),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "aiNext", onclick: () => { done = true; m.close(); resolve(true); } }, "Next"))), { onClose: () => { if (!done) resolve(false); } });
    });
    const question = q.value.trim();
    if (!ok || !question) return;
    if (!(await ask(`Ask about ${name}`, `your question and the text of up to 30 files in “${name}” (with their names)`, { okLabel: "Ask" }))) return;
    const w = working("Asking…");
    let r;
    try { r = await api("api/ai/ask", { method: "POST", body: { folder: ref, question } }); } catch (e) { w.close(); fail(e); return; }
    w.close();
    const answer = h("div", { class: "md-body ai-answer", id: "aiAnswer" });
    if (window.DocsMd) answer.appendChild(DocsMd.render(DocsMd.parse(r.answer), document, { docLink: () => ({ state: "missing" }) }));
    else answer.textContent = r.answer;
    openModal("Answer", h("div", null, h("p", { class: "hint" }, question), answer,
      r.files.length ? h("div", { class: "ai-sources" }, h("strong", null, "Files it used"),
        h("ul", null, r.files.map((f) => h("li", null, `[${f.n}] `, h("button", { class: "link-btn", type: "button", onclick: () => D.openItem(f) }, f.name)))))
        : h("p", { class: "hint" }, "It didn't name any of the files."),
      h("p", { class: "hint" }, `${r.sent.length} file${r.sent.length === 1 ? "" : "s"} were sent. Answers can be wrong — check the files.`)), { wide: true });
  }
  D.addAction({ id: "ai-ask", order: 79.7, icon: "✨", label: "Ask about this folder (AI)", show: (it) => aiOn() && it.kind === "folder" && !it.inTrash && !it.isRoot, run: (it) => askFolder(it.id, it.name) });
  D.rootActions.push({ id: "ai-ask", icon: "✨", label: "Ask about this folder (AI)", show: () => aiOn(), run: (f) => askFolder(f.id, f.name) });

  // ---------- Read text from scans in this folder ----------
  async function folderSwitch(ref, name) {
    let st;
    try { st = await api(`api/ai/folder/${encodeURIComponent(ref)}`); } catch (e) { fail(e); return; }
    const on = h("input", { type: "checkbox", id: "aiFolderOn", checked: st.on, disabled: !st.canChange });
    const m = openModal("Read text from scans", h("div", null,
      h("p", null, `When pictures or scanned PDFs arrive in “${name}” (or its folders), the app sends each of them to `, h("strong", null, aiState().provider || "the AI provider"),
        ` (model ${aiState().visionModel || "?"}) to read their text, so search finds it. A few are read each minute.`),
      st.waiting ? h("p", { class: "hint" }, `${st.waiting} picture${st.waiting === 1 ? "" : "s"} or scan${st.waiting === 1 ? "" : "s"} here would be read now.`) : null,
      h("label", { class: "mini-toggle" }, on, "Read text from scans in this folder"),
      st.canChange ? null : h("p", { class: "hint" }, st.on ? "Only people who can change this folder can switch this off." : "Only the folder's owner (or a manager) can switch this on — its pictures go to the AI provider."),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Close"),
        st.canChange ? h("button", { class: "btn-primary", type: "button", id: "aiFolderSave", onclick: async () => {
          try { await api(`api/ai/folder/${encodeURIComponent(ref)}`, { method: "PUT", body: { on: on.checked } }); m.close(); toast(on.checked ? "Scans here will be read" : "Scans here won't be read"); D.render(); } catch (e) { fail(e); }
        } }, "Save") : null)));
  }
  D.addAction({ id: "ai-folder", order: 79.8, icon: "✨", label: (it) => (it.aiScans ? "Reading text from scans here…" : "Read text from scans here…"),
    show: (it) => aiOn() && it.kind === "folder" && !it.inTrash && !it.isRoot && can(it, "editor"), run: (it) => folderSwitch(it.id, it.name) });
  D.rootActions.push({ id: "ai-folder", icon: "✨", label: (f) => (f.aiScans ? "Reading text from scans here…" : "Read text from scans here…"),
    show: (f) => aiOn() && f.role === "editor", run: (f) => folderSwitch(f.id, f.name) });

  Object.assign(D, { aiSummarise: summarise, aiAskFolder: askFolder, aiChecklistFrom: checklistFrom, aiSheetFrom: sheetFrom, aiFolderSwitch: folderSwitch });

  // ---------- Settings → You ----------
  D.settingsRows.push((me, save, row) => (aiState().on ? row("Show AI buttons", `AI is set up by an admin (${aiState().provider}). Off: the ✨ buttons are hidden for you.`,
    D.toggleSwitch(me.prefs.showAi !== false, (v) => save({ showAi: v }), "Show AI buttons")) : null));

  // =====================================================================
  // Admin: App settings → AI (Test connection, the address placeholder) and AI usage
  // =====================================================================
  if (!window.Admin) return;
  Admin.settingsHooks.push((key, page) => {
    if (key && key !== "ai_provider") return;
    const p = (page.data.providers || []).find((x) => x.id === page.value("ai_provider"));
    const input = page.row("ai_url") && page.row("ai_url").querySelector("input");
    if (input) input.placeholder = p && p.defaultUrl ? `${p.defaultUrl} (leave empty for this)` : "http://<ollama host>:11434";
  });
  Admin.settingsFields.ai_vision_model = { after: (page) => {
    const out = h("div", { class: "hint", id: "aiTestOut", role: "status" });
    const btn = h("button", { class: "btn-secondary btn-small", type: "button", id: "aiTest", onclick: async () => {
      out.textContent = "Testing…";
      try {
        const r = await api("api/admin/ai/test", { method: "POST", body: { provider: page.value("ai_provider"), url: page.value("ai_url"), model: page.value("ai_model"), apiKey: page.edits.ai_api_key || "" } });
        out.textContent = r.ok ? `Connected. ${r.models.length} model${r.models.length === 1 ? "" : "s"} offered${r.answer ? `; the text model answered ${r.answer}` : ""}.` : r.error;
        out.className = "hint " + (r.ok ? "ok" : "danger");
      } catch (e) { out.textContent = e.message; out.className = "hint danger"; }
    } }, "Test connection");
    return h("div", { class: "form-row" }, btn, out);
  } };

  Admin.addTab({ id: "ai", label: "AI usage", order: 38, render: async (box, args) => renderUsage(box, args[0]) });
  async function renderUsage(box, days) {
    days = [7, 30, 90].includes(Number(days)) ? Number(days) : 30;
    mount(box, spinner());
    let u;
    try { u = await api(`api/admin/ai/usage?days=${days}`); } catch (e) { mount(box, D.errorCard(e, () => renderUsage(box, days))); return; }
    const n = (x) => Number(x || 0).toLocaleString();
    const priced = !!(u.prices.in || u.prices.out);
    const money = (c) => (c === null || c === undefined ? null : c > 0 && c < 0.01 ? "< 0.01" : c.toFixed(2));
    const tile = (label, t, id) => h("div", { class: "usage-tile", id },
      h("div", { class: "usage-label" }, label),
      h("div", { class: "usage-big" }, n(t.tokensIn + t.tokensOut), h("span", { class: "usage-unit" }, " tokens")),
      h("div", { class: "hint" }, `${n(t.calls)} request${t.calls === 1 ? "" : "s"}${t.failed ? `, ${n(t.failed)} failed` : ""}`),
      priced ? h("div", { class: "usage-cost" }, `about ${money(t.cost) || "0.00"}`) : null);
    const chart = D.columns(u.byDay, { label: `Tokens a day over the last ${days} days`, value: (d) => d.tokensIn + d.tokensOut, name: () => "",
      title: (d) => `${d.date}: ${n(d.tokensIn + d.tokensOut)} tokens, ${n(d.calls)} request(s)` });
    const table = (rows, head, cells, id, text = 1) => (rows.length
      ? h("div", { class: "table-scroll" }, h("table", { class: "usage-table", id },
        h("thead", null, h("tr", null, head.map((x, i) => h("th", { class: i >= text ? "num" : "" }, x)))),
        h("tbody", null, rows.map((r) => h("tr", null, cells(r).map((x, i) => h("td", { class: i >= text ? "num" : "" }, x)))))))
      : h("div", { class: "hint" }, "Nothing yet."));
    const costHead = priced ? ["Cost"] : [];
    const costCell = (r) => (priced ? [money(r.cost) || "0.00"] : []);
    const picker = h("div", { class: "seg", role: "group", "aria-label": "Period" },
      [7, 30, 90].map((d) => h("button", { type: "button", class: d === days ? "active" : "", "aria-pressed": d === days ? "true" : "false", onclick: () => renderUsage(box, d) }, `${d} days`)));
    const lim = u.monthly;
    mount(box,
      h("p", { class: "hint" }, u.enabled ? "Every request this app sent to the AI model. Nothing about what was sent is kept — only what for, the model, tokens and time. " : "AI is off (Admin → App settings → AI). ",
        priced ? "Costs are estimates from the prices on App settings." : "Add your provider's prices on App settings to see an estimated cost.",
        lim.limit ? ` This month: ${n(lim.used)} of ${n(lim.limit)} tokens.` : ""),
      h("div", { class: "usage-tiles" }, tile("Today", u.totals.today, "usageToday"), tile("Last 7 days", u.totals.week, "usageWeek"),
        tile("Last 30 days", u.totals.month, "usageMonth"), tile("All time", u.totals.all, "usageAll")),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, "Tokens a day"), picker), chart,
        h("div", { class: "usage-axis" }, h("span", null, u.byDay[0].date), h("span", null, u.byDay[u.byDay.length - 1].date))),
      h("div", { class: "card" }, h("h3", null, `What for · ${days} days`),
        table(u.byPurpose, ["Action", "Requests", "Failed", "Tokens"].concat(costHead), (r) => [r.label, n(r.calls), n(r.failed), n(r.tokensIn + r.tokensOut)].concat(costCell(r)), "usageByPurpose")),
      h("div", { class: "card" }, h("h3", null, `By model · ${days} days`),
        table(u.byModel, ["Model", "Requests", "Failed", "Tokens in", "Tokens out"].concat(costHead),
          (r) => [`${r.model || "?"} (${r.provider})`, n(r.calls), n(r.failed), n(r.tokensIn), n(r.tokensOut)].concat(costCell(r)), "usageByModel")),
      h("div", { class: "card" }, h("h3", null, "Latest requests"),
        table(u.recent, ["When", "What", "Result", "Tokens", "Took"],
          (r) => [D.fmtFull(r.at), r.label, r.ok ? "OK" : `Failed: ${r.error || "?"}`, n(r.tokensIn + r.tokensOut), `${(r.ms / 1000).toFixed(1)} s`], "usageRecent", 3)));
  }
  void pageHead;
})();
