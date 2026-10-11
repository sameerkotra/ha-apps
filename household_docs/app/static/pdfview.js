"use strict";
/* Household Docs — the PDF viewer (SPEC §17.24). A PDF's pages are drawn here by PDF.js (vendor/pdfjs, Apache-2.0)
   onto canvases, from the file's bytes fetched like a download. The PDF itself is never opened as a page on Home
   Assistant's address: nothing in it runs (no PDF scripting, no eval), and its links and forms are only pictures.
   Pages are drawn when they come near the screen and let go when they're far away, so a long PDF stays light.
   ⤢ Fit, − and + zoom; "+", "-" and "0" on the keyboard too. A password-protected PDF asks for its password. */
(function () {
  const D = window.Docs;
  const { h, toast, openModal, spinner } = D;
  const { mount } = UI;
  const V = "?v=1.6.0";                       // the app's version (cache busting, checked by the packaging tests)
  const BASE = "vendor/pdfjs/";
  const MAX_BYTES = 50 * 1024 * 1024;         // bigger ones download instead
  const CSS_PER_PT = 96 / 72;                 // PDF points → CSS pixels at 100 %
  const ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4];
  let lib = null;

  const abs = (p) => new URL(p, document.baseURI).href;
  function loadLib() {
    if (!lib) {
      lib = import(abs(BASE + "pdf.min.mjs" + V)).then((m) => {
        m.GlobalWorkerOptions.workerSrc = abs(BASE + "pdf.worker.min.mjs" + V);
        return m;
      });
      lib.catch(() => { lib = null; });
    }
    return lib;
  }
  const isPdf = (it) => !!it && it.kind === "file" && String(it.ext || "").toLowerCase() === "pdf";

  async function fetchBytes(id) {
    let r;
    try { r = await fetch(`api/nodes/${encodeURIComponent(id)}/file`, { credentials: "same-origin" }); }
    catch (e) { throw new Error("Can't reach Household Docs. Check your connection and try again."); }
    if (!r.ok) {
      let msg = `The PDF couldn't be loaded (HTTP ${r.status}).`;
      try { const b = await r.json(); if (b && typeof b.detail === "string") msg = b.detail; } catch (e) { /* not JSON */ }
      throw new Error(msg);
    }
    return new Uint8Array(await r.arrayBuffer());
  }

  function viewPdf(it) {
    if ((it.size || 0) > MAX_BYTES) {
      toast(`“${it.name}” is too big to view here (over ${MAX_BYTES / 1048576} MB) — download it instead.`, true);
      return null;
    }
    const pages = h("div", { class: "pdf-pages", id: "pdfPages", tabindex: "0", "aria-label": `Pages of ${it.name}` }, spinner());
    const pageNo = h("span", { class: "pdf-page-no", id: "pdfPageNo", "aria-live": "polite" });
    const zoomOut = h("button", { class: "btn-ghost btn-small", type: "button", id: "pdfZoomOut", title: "Smaller", "aria-label": "Zoom out", disabled: true }, "−");
    const zoomIn = h("button", { class: "btn-ghost btn-small", type: "button", id: "pdfZoomIn", title: "Bigger", "aria-label": "Zoom in", disabled: true }, "+");
    const fit = h("button", { class: "btn-ghost btn-small pdf-zoom-label", type: "button", id: "pdfFit", title: "Fit to the width", disabled: true }, "Fit");
    const dl = h("a", { class: "btn-ghost btn-small", id: "pdfDownload", href: `api/nodes/${encodeURIComponent(it.id)}/file`, download: it.name }, "⬇ Download");
    const bar = h("div", { class: "pdf-bar" }, pageNo, h("span", { class: "pdf-zoom" }, zoomOut, fit, zoomIn), dl);

    let task = null, doc = null, closed = false, near = null, seen = null;
    let zoom = 1, fitMode = true, base = null;                  // base: page 1's size at 100 % (CSS px)
    const slots = [];                                            // {n, el, canvas, w, hgt, drawn, drawing, job}
    const m = openModal(it.name, h("div", { class: "pdf-view" }, bar, pages), {
      modalClass: "pdf-full", backdropClass: "pdf-backdrop", focus: false,
      onClose: () => {
        closed = true;
        document.removeEventListener("keydown", onKey);
        if (near) near.disconnect();
        if (seen) seen.disconnect();
        if (task) task.destroy().catch(() => {});
      },
    });
    m.el.id = "pdfViewer";

    function fail(msg) {
      if (closed) return;
      mount(pages, h("div", { class: "pdf-message" }, h("p", null, msg), h("p", null, dl.cloneNode(true))));
    }
    function fitZoom() {
      const avail = Math.max(120, pages.clientWidth - 24);
      return Math.min(4, Math.max(0.25, avail / base.w));
    }
    function setZoom(z, keepFit = false) {
      fitMode = keepFit;
      zoom = Math.min(4, Math.max(0.25, z));
      fit.textContent = fitMode ? "Fit" : `${Math.round(zoom * 100)} %`;
      fit.title = fitMode ? "Fits the width" : "Fit to the width";
      const top = pages.scrollTop / Math.max(1, pages.scrollHeight);       // keep the place while zooming
      for (const s of slots) layout(s);
      pages.scrollTop = top * pages.scrollHeight;
      for (const s of slots) { if (s.drawn || s.drawing) { cancel(s); s.drawn = false; } if (s.near) draw(s); }
    }
    function layout(s) {
      const w = (s.w || base.w) * zoom, hgt = (s.hgt || base.h) * zoom;
      s.el.style.width = `${Math.round(w)}px`;
      s.el.style.height = `${Math.round(hgt)}px`;
    }
    function cancel(s) {
      if (s.job) { try { s.job.cancel(); } catch (e) { /* done already */ } s.job = null; }
      s.drawing = false;
    }
    function release(s) {
      cancel(s);
      if (s.canvas) { s.canvas.width = 0; s.canvas.height = 0; s.canvas.remove(); s.canvas = null; }
      s.drawn = false;
    }
    async function draw(s) {
      if (closed || s.drawn || s.drawing) return;
      s.drawing = true;
      const z = zoom;
      try {
        const page = await doc.getPage(s.n);
        if (closed || z !== zoom || !s.near) { s.drawing = false; return; }
        const v1 = page.getViewport({ scale: CSS_PER_PT });
        if (!s.w) { s.w = v1.width; s.hgt = v1.height; layout(s); }
        const vp = page.getViewport({ scale: CSS_PER_PT * z });
        const dpr = Math.min(window.devicePixelRatio || 1, 3);
        // keep a canvas under ~16 million pixels (phones refuse bigger ones)
        const k = Math.min(dpr, Math.sqrt(16e6 / Math.max(1, vp.width * vp.height)));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.floor(vp.width * k));
        canvas.height = Math.max(1, Math.floor(vp.height * k));
        canvas.className = "pdf-canvas";
        canvas.setAttribute("aria-label", `Page ${s.n}`);
        s.job = page.render({ canvas, canvasContext: canvas.getContext("2d"), viewport: vp,
          transform: k !== 1 ? [k, 0, 0, k, 0, 0] : null });
        await s.job.promise;
        s.job = null;
        if (closed || z !== zoom) { canvas.width = 0; s.drawing = false; return; }
        if (s.canvas) s.canvas.remove();
        s.canvas = canvas;
        s.el.replaceChildren(canvas);
        s.drawn = true;
      } catch (e) {
        if (!(e && e.name === "RenderingCancelledException") && !closed) {
          s.el.replaceChildren(h("span", { class: "pdf-page-error" }, `Page ${s.n} couldn't be drawn.`));
          s.drawn = true;
        }
      }
      s.drawing = false;
    }

    function askPassword(update, wrong) {
      const input = h("input", { type: "password", id: "pdfPassword", autocomplete: "off", "aria-label": "Password" });
      const go = h("button", { class: "btn-primary", type: "submit" }, "Open");
      const form = h("form", { class: "pdf-message" },
        h("p", null, wrong ? "That password isn't right. Try again:" : "This PDF is protected with a password."),
        h("div", { class: "pdf-password" }, input, go));
      form.addEventListener("submit", (e) => { e.preventDefault(); mount(pages, spinner()); update(input.value); });
      mount(pages, form);
      input.focus();
    }

    function onKey(e) {
      if (closed || !doc || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.key === "+" || e.key === "=") { e.preventDefault(); step(1); }
      else if (e.key === "-") { e.preventDefault(); step(-1); }
      else if (e.key === "0") { e.preventDefault(); setZoom(fitZoom(), true); }
    }
    function step(dir) {
      const i = ZOOMS.findIndex((z) => z > zoom + 1e-6);
      const up = i < 0 ? ZOOMS[ZOOMS.length - 1] : ZOOMS[i];
      const below = ZOOMS.filter((z) => z < zoom - 1e-6);
      setZoom(dir > 0 ? up : (below.length ? below[below.length - 1] : ZOOMS[0]));
    }
    document.addEventListener("keydown", onKey);
    zoomIn.addEventListener("click", () => step(1));
    zoomOut.addEventListener("click", () => step(-1));
    fit.addEventListener("click", () => setZoom(fitZoom(), true));
    window.addEventListener("resize", function onResize() {
      if (closed) { window.removeEventListener("resize", onResize); return; }
      if (fitMode && base) setZoom(fitZoom(), true);
    });

    (async () => {
      let pdfjs, data;
      try { [pdfjs, data] = await Promise.all([loadLib(), fetchBytes(it.id)]); }
      catch (e) { fail(e && e.message && !/import|module/i.test(e.message) ? e.message : "The PDF viewer couldn't start in this browser. Download the PDF instead."); return; }
      if (closed) return;
      task = pdfjs.getDocument({
        data, isEvalSupported: false, enableXfa: false, useWasm: false,
        wasmUrl: abs(BASE + "wasm/"), cMapUrl: abs(BASE + "cmaps/"), cMapPacked: true,
        standardFontDataUrl: abs(BASE + "standard_fonts/"),
      });
      task.onPassword = (update, reason) => askPassword(update, reason === pdfjs.PasswordResponses.INCORRECT_PASSWORD);
      try { doc = await task.promise; }
      catch (e) {
        if (closed) return;
        fail(e && e.name === "InvalidPDFException" ? "This file isn't a PDF the viewer can read. Download it to open it elsewhere."
          : "This PDF couldn't be shown here. Download it to open it in your own viewer.");
        return;
      }
      if (closed) return;
      const first = (await doc.getPage(1)).getViewport({ scale: CSS_PER_PT });
      base = { w: first.width, h: first.height };
      for (let n = 1; n <= doc.numPages; n++) {
        const el = h("div", { class: "pdf-page", dataset: { n: String(n) } });
        slots.push({ n, el, canvas: null, w: n === 1 ? base.w : 0, hgt: n === 1 ? base.h : 0, drawn: false, drawing: false, job: null, near: false });
      }
      mount(pages, slots.map((s) => s.el));
      for (const b of [zoomIn, zoomOut, fit]) b.disabled = false;
      pageNo.textContent = `1 / ${doc.numPages}`;
      setZoom(fitZoom(), true);
      // draw pages within a screen or two of what's shown; let go of ones far away
      near = new IntersectionObserver((entries) => {
        for (const en of entries) {
          const s = slots[Number(en.target.dataset.n) - 1];
          s.near = en.isIntersecting;
          if (s.near) draw(s); else release(s);
        }
      }, { root: pages, rootMargin: "150% 0px" });
      // the page number: the page covering the middle of the view
      seen = new IntersectionObserver((entries) => {
        for (const en of entries) if (en.isIntersecting) pageNo.textContent = `${en.target.dataset.n} / ${doc.numPages}`;
      }, { root: pages, rootMargin: "-50% 0px -50% 0px" });
      for (const s of slots) { near.observe(s.el); seen.observe(s.el); }
      pages.focus({ preventScroll: true });
    })();
    return m;
  }

  D.viewPdf = viewPdf;
  D.isPdf = isPdf;
  D.addAction({ id: "view-pdf", label: "View", icon: "👁", order: 1, show: (it) => isPdf(it) && !it.inTrash, run: (it) => viewPdf(it) });
})();
