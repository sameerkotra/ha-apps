"use strict";
/* Household Docs — bringing things in (SPEC §17.5, §17.7, §17.11): 📷 Scan (the phone's camera, several pages,
   corner handles to crop and straighten, black and white, saved as one PDF built here by scanpdf.js or as JPEG
   pictures, named by the person's pattern), Import from Google Keep and Import a .zip (upload → preview → import,
   with progress), and the file panel's searchable-text line for PDFs (and, through Docs.aiTextBlock, text read by
   AI). Photos are drawn on canvases from blob: URLs (allowed by the page's CSP); nothing leaves the browser until
   Save. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, fmtSize } = D;
  const { mount, lsGet, lsSet } = UI;
  const S = window.ScanPdf;
  const MAX_SIDE = 2000;               // longest side of a page kept (pixels)
  const VIEW_MAX = 900;                // longest side of the crop view

  // =====================================================================
  // 📷 Scan
  // =====================================================================
  function canvasOf(w, h_) { const c = document.createElement("canvas"); c.width = w; c.height = h_; return c; }
  function loadPhoto(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        const s = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
        const c = canvasOf(Math.max(1, Math.round(img.naturalWidth * s)), Math.max(1, Math.round(img.naturalHeight * s)));
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve(c);
      };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error(`“${file.name}” isn't a picture this browser can open.`)); };
      img.src = url;
    });
  }
  function rotate(c) {
    const r = canvasOf(c.height, c.width);
    const g = r.getContext("2d");
    g.translate(r.width, 0); g.rotate(Math.PI / 2); g.drawImage(c, 0, 0);
    return r;
  }
  function straighten(photo, quad, bw) {
    const size = S.outputSize(quad, MAX_SIDE);
    const src = photo.getContext("2d").getImageData(0, 0, photo.width, photo.height);
    const out = canvasOf(size.width, size.height);
    const g = out.getContext("2d");
    const img = S.warp(src, quad, size.width, size.height, (w, hh) => g.createImageData(w, hh));
    if (bw) S.blackAndWhite(img);
    g.putImageData(img, 0, 0);
    return out;
  }
  function jpegBytes(c, q = 0.85) {
    return new Promise((resolve, reject) => c.toBlob((b) => (b ? b.arrayBuffer().then((a) => resolve(new Uint8Array(a)), reject) : reject(new Error("Couldn't make the picture."))), "image/jpeg", q));
  }
  function accent() { return getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#7c6cf0"; }

  // the crop editor: the photo with four handles (pointer events, so phones too)
  function cropView(page, onChange) {
    const view = canvasOf(1, 1);
    view.className = "crop-canvas";
    const wrap = h("div", { class: "crop-wrap" }, view);
    const handles = [0, 1, 2, 3].map((i) => {
      const el = h("span", { class: "crop-handle", dataset: { corner: String(i) }, role: "slider", tabindex: "0",
        "aria-label": ["Top left corner", "Top right corner", "Bottom right corner", "Bottom left corner"][i] });
      wrap.appendChild(el);
      return el;
    });
    function draw() {
      const p = page.photo;
      const s = Math.min(1, VIEW_MAX / Math.max(p.width, p.height));
      view.width = Math.round(p.width * s); view.height = Math.round(p.height * s);
      const g = view.getContext("2d");
      g.drawImage(p, 0, 0, view.width, view.height);
      g.strokeStyle = accent(); g.lineWidth = 3;
      g.beginPath();
      page.quad.forEach(([x, y], i) => (i ? g.lineTo(x * s, y * s) : g.moveTo(x * s, y * s)));
      g.closePath(); g.stroke();
      handles.forEach((el, i) => { el.style.left = (page.quad[i][0] / p.width * 100) + "%"; el.style.top = (page.quad[i][1] / p.height * 100) + "%"; });
    }
    handles.forEach((el, i) => {
      el.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        el.setPointerCapture(e.pointerId);
        const move = (ev) => {
          const r = view.getBoundingClientRect();
          const x = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * (page.photo.width - 1);
          const y = Math.max(0, Math.min(1, (ev.clientY - r.top) / r.height)) * (page.photo.height - 1);
          page.quad[i] = [x, y];
          draw();
        };
        const up = () => { el.removeEventListener("pointermove", move); el.removeEventListener("pointerup", up); onChange && onChange(); };
        el.addEventListener("pointermove", move);
        el.addEventListener("pointerup", up);
      });
      el.addEventListener("keydown", (e) => {
        const step = e.shiftKey ? 20 : 4;
        const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
        if (!d) return;
        e.preventDefault();
        page.quad[i] = [Math.max(0, Math.min(page.photo.width - 1, page.quad[i][0] + d[0])), Math.max(0, Math.min(page.photo.height - 1, page.quad[i][1] + d[1]))];
        draw();
      });
    });
    draw();
    return { el: wrap, draw };
  }

  function scanDialog(ctx) {
    const me = D.state.me;
    const pages = [];                     // {photo: canvas, quad, bw}
    let current = -1;
    const input = h("input", { type: "file", accept: "image/*", capture: "environment", multiple: true, hidden: true, id: "scanInput" });
    const stage = h("div", { class: "scan-stage", id: "scanStage" });
    const strip = h("div", { class: "scan-strip", id: "scanStrip" });
    const err = h("div", { class: "error-text", role: "alert" });
    const bwBox = h("input", { type: "checkbox", id: "scanBw", checked: lsGet("docs.scanBw") === "1" });
    const asSel = h("select", { id: "scanAs", "aria-label": "Save as" }, h("option", { value: "pdf" }, "One PDF"), h("option", { value: "jpg" }, "JPEG pictures"));
    asSel.value = lsGet("docs.scanAs") === "jpg" ? "jpg" : "pdf";
    const name = h("input", { type: "text", id: "scanName", maxlength: "120", value: S.autoName(me.prefs.scanName, new Date(), ctx.label), "aria-label": "File name" });
    const save = h("button", { class: "btn-primary", type: "button", id: "scanSave", disabled: true }, "Save");
    const addBtn = h("button", { class: "btn-secondary", type: "button", id: "scanAdd", onclick: () => input.click() }, "📷 Add a page");
    const body = h("div", { class: "scan-dialog" }, input,
      h("p", { class: "hint" }, `Saved into ${ctx.label}. Take a photo of each page (or choose pictures); drag the four corners onto the page's corners.`),
      stage, strip, err,
      h("div", { class: "form-row scan-options" }, addBtn, h("label", { class: "mini-toggle" }, bwBox, "Black and white"),
        h("label", { class: "field compact" }, "Save as", asSel)),
      h("label", { class: "field" }, "Name", name),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), save));
    const m = openModal("📷 Scan", body, { wide: true, focus: false });

    function drawStrip() {
      mount(strip, pages.map((p, i) => {
        const thumb = canvasOf(1, 1);
        const s = 96 / Math.max(p.photo.width, p.photo.height);
        thumb.width = Math.max(1, Math.round(p.photo.width * s)); thumb.height = Math.max(1, Math.round(p.photo.height * s));
        thumb.getContext("2d").drawImage(p.photo, 0, 0, thumb.width, thumb.height);
        return h("div", { class: "scan-thumb" + (i === current ? " current" : ""), dataset: { page: String(i + 1) } },
          h("button", { class: "thumb-btn", type: "button", "aria-label": `Page ${i + 1}`, onclick: () => show(i) }, thumb, h("span", { class: "hint" }, `Page ${i + 1}`)),
          h("button", { class: "icon-btn", type: "button", "aria-label": `Remove page ${i + 1}`, title: "Remove", onclick: () => { pages.splice(i, 1); show(Math.min(i, pages.length - 1)); } }, "✕"));
      }));
      save.disabled = !pages.length;
    }
    function show(i) {
      current = i;
      if (i < 0) { mount(stage, h("div", { class: "empty" }, "No pages yet — ", h("button", { class: "link-btn", type: "button", onclick: () => input.click() }, "take a photo"), ".")); drawStrip(); return; }
      const page = pages[i];
      const v = cropView(page);
      mount(stage, v.el, h("div", { class: "form-row" },
        h("button", { class: "btn-ghost btn-small", type: "button", id: "scanRotate", onclick: () => { page.photo = rotate(page.photo); page.quad = S.suggestQuad(page.photo.width, page.photo.height); show(i); } }, "↻ Rotate"),
        h("button", { class: "btn-ghost btn-small", type: "button", id: "scanWhole", onclick: () => { const w = page.photo.width - 1, hh = page.photo.height - 1; page.quad = [[0, 0], [w, 0], [w, hh], [0, hh]]; v.draw(); } }, "Whole photo")));
      drawStrip();
    }
    input.addEventListener("change", async () => {
      err.textContent = "";
      for (const f of Array.from(input.files)) {
        try {
          const photo = await loadPhoto(f);
          pages.push({ photo, quad: S.suggestQuad(photo.width, photo.height) });
        } catch (e) { err.textContent = e.message; }
      }
      input.value = "";
      show(pages.length - 1);
    });
    save.addEventListener("click", async () => {
      err.textContent = "";
      if (!pages.length) return;
      lsSet("docs.scanBw", bwBox.checked ? "1" : "0");
      lsSet("docs.scanAs", asSel.value);
      save.disabled = true;
      save.textContent = "Saving…";
      try {
        const jpegs = [];
        for (const p of pages) jpegs.push(await jpegBytes(straighten(p.photo, p.quad, bwBox.checked), bwBox.checked ? 0.8 : 0.85));
        const ref = ctx.folderId || "mine";
        const base = name.value.trim() || S.autoName(me.prefs.scanName, new Date(), ctx.label);
        const saved = [];
        if (asSel.value === "pdf") {
          const pdf = S.buildPdf(jpegs.map((j) => ({ jpeg: j })));
          saved.push(await api(`api/nodes/${encodeURIComponent(ref)}/scan?type=pdf&name=${encodeURIComponent(base)}`, { method: "POST", rawBody: new Blob([pdf], { type: "application/pdf" }), headers: { "Content-Type": "application/pdf" } }));
        } else {
          for (let i = 0; i < jpegs.length; i++) {
            const n = jpegs.length > 1 ? `${base} ${i + 1}` : base;
            saved.push(await api(`api/nodes/${encodeURIComponent(ref)}/scan?type=jpg&name=${encodeURIComponent(n)}`, { method: "POST", rawBody: new Blob([jpegs[i]], { type: "image/jpeg" }), headers: { "Content-Type": "image/jpeg" } }));
          }
        }
        m.close();
        toast(saved.length === 1 ? `${saved[0].name} saved in ${ctx.label}` : `${saved.length} pictures saved in ${ctx.label}`);
        if (D.state.route === "folder" || D.state.route === "mine") D.render();
        if (D.aiOffer) D.aiOffer(saved);           // with AI on: Read text straight after (§17.6)
      } catch (e) {
        err.textContent = e.message;
        save.disabled = false;
        save.textContent = "Save";
      }
    });
    show(-1);
    setTimeout(() => input.click(), 50);           // straight to the camera on phones
  }
  D.scanDialog = scanDialog;
  D.settingsRows.push((me, save, row) => {
    const input = h("input", { type: "text", id: "prefScanName", maxlength: "120", value: me.prefs.scanName || "Scan {date} {time}", "aria-label": "Scan file names" });
    const example = h("div", { class: "sub hint", id: "scanNameExample" });
    const show = () => { example.textContent = "e.g. " + S.autoName(input.value, new Date(), "Taxes", 1); };
    input.addEventListener("input", show);
    input.addEventListener("change", () => { if (input.value.trim()) save({ scanName: input.value.trim() }); });
    show();
    return row("Scan file names", "📷 Scan names a new scan with this: {date}, {time}, {folder} (the folder it's saved in) and {n} (the page).", h("div", { class: "pref-col" }, input, example));
  });
  D.addNew({ id: "scan", icon: "📷", label: "Scan", order: 82, run: (ctx) => scanDialog(ctx) });

  // =====================================================================
  // Imports: Google Keep and .zip
  // =====================================================================
  function pollJob(id, bar, label) {
    return new Promise((resolve, reject) => {
      const tick = async () => {
        try {
          const j = await api(`api/import/jobs/${encodeURIComponent(id)}`);
          if (j.total) { bar.max = j.total; bar.value = j.done; label.textContent = `${j.done} of ${j.total}`; }
          if (j.state === "done") return resolve(j.result);
          if (j.state === "failed") return reject(new Error(j.error || "The import failed."));
          setTimeout(tick, 600);
        } catch (e) { reject(e); }
      };
      tick();
    });
  }
  function importDialog(kind, ctx) {
    const keep = kind === "keep";
    const file = h("input", { type: "file", accept: ".zip,application/zip", id: "importFile", "aria-label": "The .zip file" });
    const err = h("div", { class: "error-text", role: "alert" });
    const preview = h("div", { class: "import-preview", id: "importPreview" });
    const go_ = h("button", { class: "btn-primary", type: "button", id: "importGo", hidden: true }, "Import");
    const bar = h("progress", { class: "import-progress", hidden: true, max: "1", value: "0" });
    const barLabel = h("span", { class: "hint" });
    let token = null;
    const intro = keep
      ? [h("p", null, "In Google Takeout (takeout.google.com) choose only ", h("strong", null, "Keep"), ", export, and choose the .zip it gives you here."),
        h("p", { class: "hint" }, "Notes become notes, lists become checklists (ticks kept) in My docs → Google Keep; labels become tags, colours and pins come along, archived notes go to “Keep archive”, notes in Keep's bin are skipped. Importing again skips what's already here.")]
      : [h("p", null, `Everything in the .zip is unpacked into a new folder in ${ctx.label}.`),
        h("p", { class: "hint" }, "Hidden files, links and anything that would land outside the folder are skipped; names Windows can't hold are cleaned.")];
    const m = openModal(keep ? "Import from Google Keep" : "Import a .zip", h("div", { class: "import-dialog" }, intro,
      h("div", { class: "form-row" }, file), err, preview, h("div", null, bar, " ", barLabel),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Close"), go_)), { focus: false });
    file.addEventListener("change", async () => {
      err.textContent = ""; mount(preview, spinner()); go_.hidden = true;
      const f = file.files[0];
      if (!f) { mount(preview); return; }
      try {
        const r = await api(`api/import/${kind}/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", rawBody: f, headers: { "Content-Type": "application/zip" } });
        token = r.token;
        const p = r.preview;
        if (keep) {
          mount(preview, h("ul", { class: "import-counts" },
            h("li", null, `${p.notes} note${p.notes === 1 ? "" : "s"} and ${p.checklists} list${p.checklists === 1 ? "" : "s"} to import`),
            p.archived ? h("li", null, `${p.archived} archived (into “Keep archive”)`) : null,
            p.pinned ? h("li", null, `${p.pinned} pinned (become ⭐ Favourites)`) : null,
            p.attachments ? h("li", null, `${p.attachments} attached picture${p.attachments === 1 ? "" : "s"}`) : null,
            p.labels.length ? h("li", null, `Labels → tags: ${p.labels.join(", ")}`) : null,
            p.already ? h("li", null, `${p.already} already imported (skipped)`) : null,
            p.trashed ? h("li", null, `${p.trashed} in Keep's bin (skipped)`) : null,
            p.empty ? h("li", null, `${p.empty} empty (skipped)`) : null));
          go_.hidden = !p.toImport;
          go_.textContent = `Import ${p.toImport}`;
        } else {
          mount(preview, h("ul", { class: "import-counts" },
            h("li", null, `${p.files} file${p.files === 1 ? "" : "s"} in ${p.folders} folder${p.folders === 1 ? "" : "s"}, ${fmtSize(p.bytes)}`),
            h("li", null, `into a new folder “${p.folderName}” in ${ctx.label}`),
            p.skippedCount ? h("li", null, `${p.skippedCount} skipped: `, p.skipped.slice(0, 5).map((s) => `${s.name} (${s.why})`).join(", ")) : null));
          go_.hidden = !p.files;
        }
      } catch (e) { mount(preview); err.textContent = e.message; }
    });
    go_.addEventListener("click", async () => {
      err.textContent = ""; go_.disabled = true; file.disabled = true; bar.hidden = false;
      try {
        const j = await api(`api/import/${kind}/${encodeURIComponent(token)}`, { method: "POST", body: keep ? {} : { parentId: ctx.folderId } });
        const res = await pollJob(j.id, bar, barLabel);
        m.close();
        toast(keep ? `${res.added} note${res.added === 1 ? "" : "s"} imported${res.attachments ? ` with ${res.attachments} picture${res.attachments === 1 ? "" : "s"}` : ""}`
          : `${res.added} file${res.added === 1 ? "" : "s"} imported${res.skipped ? `, ${res.skipped} skipped` : ""}`);
        if (res.folderId) D.go("#/folder/" + encodeURIComponent(res.folderId)); else D.render();
      } catch (e) { err.textContent = e.message; go_.disabled = false; file.disabled = false; }
    });
  }
  D.importDialog = importDialog;
  D.addNew({ id: "import-keep", icon: "🗒", label: "Import from Google Keep", order: 87, show: () => !!(D.state.me.extras || {}).keepImport, run: (ctx) => importDialog("keep", ctx) });
  D.addNew({ id: "import-zip", icon: "🗜", label: "Import a .zip", order: 88, run: (ctx) => importDialog("zip", ctx) });

  // =====================================================================
  // the file panel: searchable text (PDFs; text read by AI)
  // =====================================================================
  D.filePanelExtras = D.filePanelExtras || [];
  D.filePanelExtras.push((info, modal) => {
    const isPdf = (info.ext || "").toLowerCase() === "pdf";
    if (!isPdf && !info.preview && !info.aiText) return null;
    const box = h("div", { class: "text-box", id: "fileText" });
    const load = async () => {
      let t;
      try { t = await api(`api/nodes/${encodeURIComponent(info.id)}/text`); } catch (e) { mount(box); return; }
      mount(box,
        t.pdf ? h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Search"), h("div", { class: "kv-value", id: "pdfStatus" },
          t.pdf.status === "ok" ? `Text found on ${t.pdf.pages} page${t.pdf.pages === 1 ? "" : "s"} — search finds it` : t.pdf.label)) : null,
        D.aiTextBlock ? D.aiTextBlock(info, t, load, modal) : null);
    };
    load();
    return box;
  });
})();
