"use strict";
/* Household Docs — organise (SPEC §17.1, §17.12, §17.13): tags and colours (the dialog, ⋯ → Tags and colour, the
   selection bar's Tag, 🏷 Tags in the sidebar and its page), pins on the home page (cards with previews, drag to
   reorder, ⋯ → Pin to home), ⚡ Quick note (the button, N, and the address #quick-note), and print / PDF for notes,
   Markdown notes and checklists (the print-only page, Download as PDF, Save PDF here). */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, spinner, pageHead, fmtWhen, titleOf, go } = D;
  const { mount } = UI;
  const COLOURS = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "grey"];
  const rank = { viewer: 1, editor: 2, manager: 3, owner: 4 };
  const can = (it, role) => (rank[it.role] || 0) >= rank[role];
  const readOnlyMode = () => !!(D.state.me && D.state.me.readOnly);
  D.COLOURS = COLOURS;

  // =====================================================================
  // tags and colours
  // =====================================================================
  async function knownTags() {
    try { return (await api("api/tags")).tags.map((t) => t.tag); } catch (e) { return []; }
  }
  function swatches(current, onPick, allowKeep) {
    const box = h("div", { class: "swatches", role: "radiogroup", "aria-label": "Colour" });
    const opts = (allowKeep ? [["keep", "Keep as it is"]] : []).concat([["", "No colour"]], COLOURS.map((c) => [c, c[0].toUpperCase() + c.slice(1)]));
    let value = current;
    const draw = () => mount(box, opts.map(([v, label]) => h("button", { type: "button", role: "radio", "aria-checked": String(value === v),
      class: "swatch" + (v && v !== "keep" ? " dot-" + v : v === "keep" ? " keep" : " none") + (value === v ? " on" : ""), title: label, "aria-label": label,
      dataset: { colour: v }, onclick: () => { value = v; draw(); onPick(v); } }, v === "keep" ? "=" : v ? "" : "∅")));
    draw();
    return box;
  }
  function tagInput(list, suggestions, onChange) {
    const input = h("input", { type: "text", maxlength: "40", placeholder: "Add a tag…", "aria-label": "Add a tag", list: "tagSuggest", id: "tagInput", autocomplete: "off" });
    const dl = h("datalist", { id: "tagSuggest" }, suggestions.map((t) => h("option", { value: t })));
    const chips = h("div", { class: "tag-edit-chips", id: "tagChips" });
    const draw = () => mount(chips, list.length ? list.map((t) => h("span", { class: "tag-chip edit" }, "#" + t,
      h("button", { type: "button", class: "tag-x", "aria-label": `Remove ${t}`, onclick: () => { list.splice(list.indexOf(t), 1); draw(); onChange(); } }, "✕")))
      : h("span", { class: "hint" }, "No tags yet."));
    const add = () => {
      const t = input.value.replace(/^#/, "").replace(/[",]/g, " ").replace(/\s+/g, " ").trim().slice(0, 40);
      input.value = "";
      if (t && !list.some((x) => x.toLowerCase() === t.toLowerCase())) { list.push(t); draw(); onChange(); }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } });
    input.addEventListener("change", () => { if (input.value.trim()) add(); });
    draw();
    return { el: h("div", { class: "tag-edit" }, chips, h("div", { class: "form-row" }, input, h("button", { class: "btn-ghost", type: "button", onclick: add }, "Add")), dl), add };
  }
  async function tagDialog(it, onDone) {
    const body = h("div", null, spinner());
    const m = openModal(`Tags and colour — ${titleOf(it)}`, body, { focus: false });
    let cur, sugg;
    try { [cur, sugg] = await Promise.all([api(`api/nodes/${encodeURIComponent(it.id)}/tags`), knownTags()]); }
    catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
    const list = cur.tags.slice();
    let colour = cur.color || "";
    const err = h("div", { class: "error-text", role: "alert" });
    const ti = tagInput(list, sugg, () => {});
    mount(body, h("div", { class: "field" }, "Tags", ti.el), h("div", { class: "field" }, "Colour", swatches(colour, (v) => { colour = v; })), err,
      h("p", { class: "hint" }, "Everyone who can see it sees its tags and colour. Search with tag:name or color:red."),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "tagsSave", onclick: async () => {
          ti.add();
          try {
            await api(`api/nodes/${encodeURIComponent(it.id)}/tags`, { method: "PUT", body: { tags: list, color: colour || null } });
            m.close(); toast("Saved"); D.refreshMe().catch(() => {});
            if (onDone) onDone(); else D.render();
          } catch (e) { err.textContent = e.message; }
        } }, "Save")));
  }
  async function bulkTagDialog(items, sel) {
    const sugg = await knownTags();
    const add = [], remove = [];
    let colour = "keep";
    const err = h("div", { class: "error-text", role: "alert" });
    const a = tagInput(add, sugg, () => {}), r = tagInput(remove, sugg, () => {});
    const m = openModal(`Tag ${items.length} item${items.length === 1 ? "" : "s"}`, h("div", null,
      h("div", { class: "field" }, "Add these tags", a.el), h("div", { class: "field" }, "Take these tags off", r.el),
      h("div", { class: "field" }, "Colour", swatches(colour, (v) => { colour = v; }, true)), err,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "bulkTagGo", onclick: async () => {
          a.add(); r.add();
          if (!add.length && !remove.length && colour === "keep") { err.textContent = "Add a tag, take one off, or choose a colour."; return; }
          m.close();
          let ok = 0; const errors = [];
          for (const it of items) {
            const body = { add, remove };
            if (colour !== "keep") body.color = colour || null;
            try { await api(`api/nodes/${encodeURIComponent(it.id)}/tags`, { method: "POST", body }); ok += 1; } catch (e) { errors.push(`${it.name}: ${e.message}`); }
          }
          if (errors.length) toast(errors.slice(0, 2).join(" · "), true);
          if (ok) toast(`Tagged ${ok}`);
          sel.clear(); D.refreshMe().catch(() => {}); D.render();
        } }, "Apply"))), { focus: false });
    void m;
  }
  D.tagDialog = tagDialog;
  D.addAction({ id: "tags", label: "Tags and colour…", icon: "🏷", order: 45, show: (it) => !it.isRoot && !it.inTrash && can(it, "editor") && !readOnlyMode(), run: (it) => tagDialog(it, () => D.render()) });
  D.addBulk({ id: "tag", label: "Tag…", icon: "🏷", order: 45, show: (l) => l.every((it) => can(it, "editor") && !it.inTrash && !it.isRoot), run: (l, sel) => bulkTagDialog(l, sel) });

  // 🏷 Tags: the sidebar (with counts) and #/tags
  D.addSpace({ id: "tags", icon: "🏷", label: "Tags", order: 62, hash: "#/tags",
    show: () => !!(D.state.me && D.state.me.tags && D.state.me.tags.length),
    children: () => ((D.state.me && D.state.me.tags) || []).slice().sort((a, b) => b.count - a.count).slice(0, 8)
      .map((t) => ({ id: "tag-" + t.tag, label: `#${t.tag} (${t.count})`, hash: D.searchHash({ tag: [t.tag] }) })) });
  D.route("tags", async (page, _a, current) => {
    mount(page, pageHead("Tags"), spinner());
    const d = await api("api/tags");
    if (!current()) return;
    const max = Math.max(1, ...d.tags.map((t) => t.count));
    mount(page, pageHead("Tags"),
      h("p", { class: "hint" }, "Tags on everything you can open. Tap one to see what carries it. Add tags with ⋯ → Tags and colour, or tick several items and use 🏷 Tag."),
      h("div", { class: "card" }, d.tags.length ? h("div", { class: "tag-cloud", id: "tagCloud" }, d.tags.map((t) => h("a", { class: "tag-chip big", href: D.searchHash({ tag: [t.tag] }),
        style: `font-size:${(0.85 + 0.5 * t.count / max).toFixed(2)}rem` }, "#" + t.tag, h("span", { class: "tag-count" }, String(t.count)))))
        : h("div", { class: "empty" }, "No tags yet.")),
      h("div", { class: "card" }, h("h3", null, "Colours"), h("div", { class: "chip-links" }, COLOURS.map((c) => h("a", { class: "folder-chip", href: D.searchHash({ color: c }) },
        h("span", { class: "color-dot inline dot-" + c }), " " + c)))));
  });

  // =====================================================================
  // pins (the home page) and ⋯ → Pin to home
  // =====================================================================
  function pinPreview(it) {
    const p = it.pinPreview || {};
    if (it.kind === "checklist") {
      const total = (p.open || 0) + (p.done || 0);
      return h("div", { class: "pin-preview" }, h("div", { class: "pin-progress", role: "img", "aria-label": `${p.done || 0} of ${total} done` },
        h("span", { style: `width:${total ? Math.round(100 * (p.done || 0) / total) : 0}%` })), h("span", null, total ? `${p.done || 0} of ${total} done` : "No items yet"));
    }
    if (it.kind === "folder") return h("div", { class: "pin-preview" }, `${p.items || 0} item${p.items === 1 ? "" : "s"}`);
    if (p.cell) {
      const c = p.cell;
      let text = "";
      if (c.error) text = "—";
      else if (window.SheetCalc) {
        const me = D.state.me;
        text = SheetCalc.format(c.v === undefined ? null : c.v, { f: c.f, d: c.d, red: c.red },
          { currency: (me.app && me.app.currency) || "EUR", numStyle: (me.prefs && me.prefs.numberStyle) || "auto" }).text;
      } else text = String(c.v ?? "");
      return h("div", { class: "pin-preview cell" }, h("span", { class: "pin-cell-label" }, c.label || `${c.tab} › ${c.ref}`), h("strong", { class: "pin-cell-value" }, text || "(empty)"));
    }
    if (p.lines && p.lines.length) return h("div", { class: "pin-preview lines" }, p.lines.map((l) => h("div", null, l)));
    return h("div", { class: "pin-preview" }, it.modified ? `changed ${fmtWhen(it.modified)}` : "");
  }
  D.pinsSection = function () {
    const box = h("section", { class: "card pins-card", id: "pinsCard", hidden: true });
    (async () => {
      let d;
      try { d = await api("api/me/pins"); } catch (e) { return; }
      if (!d.pins.length) return;
      let pins = d.pins;
      const grid = h("div", { class: "pin-grid", id: "pinGrid" });
      const draw = () => mount(grid, pins.map((it) => {
        const handle = h("span", { class: "pin-handle", title: "Drag to reorder", "aria-hidden": "true" }, "⠿");
        const card = h("div", { class: "pin" + (it.color ? " tint-border-" + it.color : ""), dataset: { id: it.id } },
          h("div", { class: "pin-top" }, D.iconBox(it, "pin-icon"), h("button", { class: "pin-title link-btn", type: "button", onclick: () => D.openItem(it), title: it.name }, titleOf(it)), handle,
            h("button", { class: "icon-btn pin-more", type: "button", "aria-label": `More for ${it.name}`, onclick: (e) => D.itemMenu(e.currentTarget, it, {}) }, "⋯")),
          pinPreview(it));
        dragOrder(card, handle);
        return card;
      }));
      async function saveOrder(ids) {
        try { const r = await api("api/me/pins", { method: "PUT", body: { ids } }); pins = r.pins; draw(); }
        catch (e) { fail(e); D.render(); }
      }
      function dragOrder(card, handle) {
        // pointer events: a mouse, a pen or a finger on the handle
        // listened for on the document: the card moves in the page while it's dragged, which would end a
        // pointer capture on the handle
        handle.addEventListener("pointerdown", (e) => {
          if (e.button !== undefined && e.button !== 0) return;
          e.preventDefault();
          card.classList.add("dragging");
          const id = e.pointerId;
          const move = (ev) => {
            if (ev.pointerId !== id) return;
            ev.preventDefault();
            const over = document.elementFromPoint(ev.clientX, ev.clientY);
            const target = over && over.closest ? over.closest(".pin") : null;
            if (target && target !== card && target.parentNode === grid) {
              const r = target.getBoundingClientRect();
              const after = ev.clientX > r.left + r.width / 2 || (ev.clientY > r.top + r.height * 0.75);
              grid.insertBefore(card, after ? target.nextSibling : target);
            }
          };
          const up = (ev) => {
            if (ev.pointerId !== id) return;
            document.removeEventListener("pointermove", move);
            document.removeEventListener("pointerup", up);
            document.removeEventListener("pointercancel", up);
            card.classList.remove("dragging");
            const ids = Array.from(grid.children).map((x) => x.dataset.id);
            if (ids.join() !== pins.map((x) => x.id).join()) {
              pins = ids.map((x) => pins.find((q) => q.id === x));
              saveOrder(ids);
            }
          };
          document.addEventListener("pointermove", move, { passive: false });
          document.addEventListener("pointerup", up);
          document.addEventListener("pointercancel", up);
        });
      }
      draw();
      box.hidden = false;
      mount(box, h("div", { class: "card-head" }, h("h3", null, "📌 Pinned"), h("span", { class: "hint" }, `${pins.length} of ${d.max} · drag ⠿ to reorder`)), grid);
    })();
    return box;
  };
  async function pinCellAsk(it) {
    const tab = h("input", { type: "text", id: "pinTab", value: "Sheet1", maxlength: "31", "aria-label": "Tab" });
    const ref = h("input", { type: "text", id: "pinRef", placeholder: "B4", maxlength: "10", "aria-label": "Cell" });
    return new Promise((resolve) => {
      let done = false;
      const err = h("div", { class: "error-text", role: "alert" });
      const m = openModal(`Pin “${titleOf(it)}”`, h("form", { onsubmit: (e) => {
        e.preventDefault();
        const r = ref.value.trim().toUpperCase();
        if (r && !/^[A-Z]{1,3}[1-9]\d{0,5}$/.test(r)) { err.textContent = "Type a cell like B4 (or leave it empty)."; return; }
        done = true; m.close(); resolve(r ? { tab: tab.value.trim() || "Sheet1", ref: r } : "none");
      } }, h("p", { class: "hint" }, "The card on your home page can show one cell — like the budget left this month. Leave it empty for no cell."),
        h("div", { class: "form-row" }, h("label", { class: "field" }, "Tab", tab), h("label", { class: "field" }, "Cell", ref)), err,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), h("button", { class: "btn-primary", type: "submit", id: "pinGo" }, "Pin"))),
      { onClose: () => { if (!done) resolve(null); } });
      setTimeout(() => ref.focus(), 40);
    });
  }
  async function togglePin(it) {
    try {
      if (it.pinned) { await api(`api/nodes/${encodeURIComponent(it.id)}/pin`, { method: "POST", body: { value: false } }); toast("Unpinned"); }
      else {
        let cell = null;
        if (it.kind === "sheet") { cell = await pinCellAsk(it); if (cell === null) return; if (cell === "none") cell = null; }
        await api(`api/nodes/${encodeURIComponent(it.id)}/pin`, { method: "POST", body: { value: true, cell } });
        toast("Pinned to your home page");
      }
      it.pinned = !it.pinned;
      D.render();
    } catch (e) { fail(e); }
  }
  D.addAction({ id: "pin", order: 12, icon: "📌", label: (it) => (it.pinned ? "Unpin from home" : "Pin to home"), show: (it) => !it.isRoot && !it.inTrash, run: (it) => togglePin(it) });

  // =====================================================================
  // ⚡ Quick note (§17.13)
  // =====================================================================
  let quickBusy = false;
  D.quickNote = async function () {
    if (quickBusy) return;
    if (readOnlyMode()) { toast("Documents are being moved — notes can't be made right now.", true); return; }
    quickBusy = true;
    try {
      const it = await api("api/quick-note", { method: "POST" });
      D.go(`#/doc/${encodeURIComponent(it.id)}/quick`);
    } catch (e) { fail(e); }
    quickBusy = false;
  };
  // #quick-note (a Home Assistant dashboard button or a phone shortcut opens it straight away)
  D.route("quick-note", async (page) => {
    mount(page, spinner());
    try {
      const it = await api("api/quick-note", { method: "POST" });
      history.replaceState(history.state, "", `#/doc/${encodeURIComponent(it.id)}/quick`);
      D.render();
    } catch (e) { mount(page, D.errorCard(e, () => D.render())); }
  });
  /** Leaving a quick note: name it after its first line (the server does it, and Trashes an empty one). */
  D.finishQuickNote = async function (id, keepalive) {
    try {
      const r = await api(`api/quick-note/${encodeURIComponent(id)}/finish`, { method: "POST", keepalive: !!keepalive });
      if (r.renamed) toast(`Saved as “${r.name.replace(/\.(txt|md)$/, "")}”`);
      else if (r.deleted) toast("Empty quick note — not kept");
    } catch (e) { /* the note keeps its quick name */ }
  };

  // =====================================================================
  // print and PDF (§17.12): notes, Markdown notes and checklists (sheets print from their own editor)
  // =====================================================================
  const PRINTABLE = ["note", "markdown", "checklist"];
  async function printDoc(it) {
    let doc;
    try { doc = await api(`api/docs/${encodeURIComponent(it.id)}`); } catch (e) { fail(e); return; }
    const old = document.getElementById("printArea");
    if (old) old.remove();
    const area = h("div", { id: "printArea", class: "print-area print-doc" });
    area.appendChild(h("div", { class: "print-head" }, h("h1", null, titleOf(doc)),
      h("div", { class: "print-meta" }, [doc.rootKind === "shared" ? doc.rootLabel : doc.ownerName, doc.modified ? `changed ${D.fmtFull(doc.modified)}` : null,
        `printed ${new Date().toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}`].filter(Boolean).join(" · "))));
    if (doc.kind === "checklist") {
      area.appendChild(h("ul", { class: "print-checklist" }, (doc.items || []).map((x) => h("li", { class: (x.done ? "done" : "") + (x.level ? " indent" : "") },
        h("span", { class: "print-box", "aria-hidden": "true" }, x.done ? "☑" : "☐"), " ", x.text))));
    } else if (doc.kind === "markdown" && window.DocsMd) {
      const md = h("div", { class: "md-body print-md" });
      md.appendChild(DocsMd.render(DocsMd.parse(doc.text || ""), document, { docLink: () => null }));
      area.appendChild(md);
    } else area.appendChild(h("div", { class: "print-text" }, doc.text || ""));
    document.body.appendChild(area);
    document.documentElement.classList.add("printing");
    const done = () => { document.documentElement.classList.remove("printing"); area.remove(); window.removeEventListener("afterprint", done); };
    window.addEventListener("afterprint", done);
    setTimeout(() => window.print(), 50);
  }
  async function downloadPdf(it) {
    try {
      const res = await fetch(`api/docs/${encodeURIComponent(it.id)}/pdf`, { credentials: "same-origin" });
      if (!res.ok) { let m = "Couldn't make the PDF."; try { const b = await res.json(); m = typeof b.detail === "string" ? b.detail : m; } catch (x) { /* not JSON */ } throw new Error(m); }
      const bad = parseInt(res.headers.get("X-Docs-Unsupported") || "0", 10);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = h("a", { href: url, download: titleOf(it) + ".pdf" });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      if (bad) toast(`${bad} character${bad === 1 ? "" : "s"} (other alphabets or emoji) show as “?” in this PDF — use Print → Save as PDF to keep them.`, false, { ms: 8000 });
    } catch (e) { fail(e); }
  }
  async function savePdfHere(it) {
    try {
      const r = await api(`api/docs/${encodeURIComponent(it.id)}/pdf`, { method: "POST" });
      toast(`Saved “${r.name}” beside it` + (r.unsupported ? ` (${r.unsupported} characters show as “?” — Print keeps them)` : ""), false, { onclick: () => D.openItem(r), ms: 6000 });
      if (D.state.route === "folder" || D.state.route === "mine") D.render();
    } catch (e) { fail(e); }
  }
  Object.assign(D, { printDoc, downloadPdf, savePdfHere });
  D.addAction({ id: "print", label: "Print or save as PDF", icon: "🖨", order: 73, show: (it) => PRINTABLE.includes(it.kind), run: (it) => printDoc(it) });
  D.addAction({ id: "pdf", label: "Download as PDF", icon: "📄", order: 74, show: (it) => PRINTABLE.includes(it.kind), run: (it) => downloadPdf(it) });
  D.addAction({ id: "pdf-here", label: "Save PDF here", icon: "📥", order: 74.5, show: (it) => PRINTABLE.includes(it.kind) && !readOnlyMode() && (it.role === "owner" || !!it.parentId || (it.parentRef && String(it.parentRef).startsWith("root:"))), run: (it) => savePdfHere(it) });
})();
