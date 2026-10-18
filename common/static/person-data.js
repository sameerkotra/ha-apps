/* One person's data: the Admin dialogs to export, import and delete it (PERSON_DATA_SPEC.md §2). Needs common/ui.js
 * and common/person-data.css. Every app answers the same API under its page (relative paths, for ingress):
 *
 *   GET  api/admin/people/{id}/data            → summary (below): what they have, the options, what a delete keeps
 *   POST api/admin/people/{id}/data/export     {options} → the .zip, or {job} for a big one
 *   GET  api/admin/people/data/jobs/{job}      → {state: running|done|failed, done, total, filename, error?}
 *   GET  api/admin/people/data/jobs/{job}/file → the finished .zip
 *   POST api/admin/people/data/import          multipart "file" → {token, preview}
 *   POST api/admin/people/{id}/data/import/{token}  {mode: add|replace, confirm?, options} → {message}
 *   POST api/admin/people/{id}/data/delete     {confirm, forget, choices, options} → {message}
 *
 *   summary  = {person: {id, name}, counts: [{key, label, count, detail?}], exportOptions: [{key, label, default,
 *               help?}], exportNote?, keeps: [text], cannotReach: [text], deleteChoices: [{key, label, help?, default,
 *               options: [{value, label, help?}]}], forgetNote?, deleteNote?, deleteLead? (the line above the counts)}
 *   preview  = {app, appVersion, exportedAt, exportedBy, person: {id, name, username?}, counts: [...], warnings: [text],
 *               options: [{key, label, default, help?}], target: id | null}
 *
 * Use:
 *   PeopleData.menu(person, cfg)       a "⋯ Data" button with Export / Import into / Delete, for a person's card or row
 *   PeopleData.importButton(cfg)       "Import a person's data…", for above the list
 *   PeopleData.open(person, "export" | "import" | "delete", cfg)
 *   cfg = {people: [{id, name}] (import targets), onDone() (after an import or delete), appName?}
 */
(function () {
  "use strict";
  const { h } = UI;
  const BASE = "api/admin/people";

  function root() {
    let r = document.getElementById("pdRoot");
    if (!r) { r = h("div", { id: "pdRoot" }); document.body.appendChild(r); }
    return r;
  }
  function modal(title, content, opts = {}) {
    return UI.openModal(title, content, Object.assign({ root: root(), modalClass: "pd-modal", backdropClass: "pd-backdrop",
      escape: "top" }, opts));
  }
  function btn(label, cls, onclick, attrs = {}) {
    return h("button", Object.assign({ type: "button", class: "pd-btn " + (cls || ""), onclick }, attrs), label);
  }
  async function call(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
    if (opts.json !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.json); }
    if (opts.form) init.body = opts.form;
    let res;
    try { res = await fetch(path, init); } catch (e) { throw new Error("The app couldn't be reached."); }
    if (!res.ok) {
      let msg = res.statusText || "Something went wrong.";
      try { const b = await res.json(); msg = (b && (b.detail || b.error)) || msg; if (typeof msg !== "string") msg = JSON.stringify(msg); }
      catch (e) { /* not JSON */ }
      const err = new Error(msg); err.status = res.status; throw err;
    }
    return res;
  }
  async function json(path, opts) { return (await call(path, opts)).json(); }
  function saveBlob(blob, filename) {
    const a = h("a", { href: URL.createObjectURL(blob), download: filename || "person-data.zip" });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  }
  function filenameOf(res) {
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
    return m ? decodeURIComponent(m[1]) : "person-data.zip";
  }
  function countsList(counts) {
    if (!counts || !counts.length) return h("p", { class: "pd-hint" }, "Nothing here yet.");
    return h("ul", { class: "pd-counts" }, counts.map((c) => h("li", null,
      h("span", { class: "pd-count" }, String(c.count)),
      h("span", null, c.label, c.detail ? h("span", { class: "pd-hint" }, " · " + c.detail) : null))));
  }
  function checks(options, values) {
    return (options || []).map((o) => {
      const cb = h("input", { type: "checkbox", checked: o.default ? true : null });
      values[o.key] = !!o.default;
      cb.addEventListener("change", () => { values[o.key] = cb.checked; });
      return h("label", { class: "pd-check" }, cb, h("span", null, o.label, o.help ? h("span", { class: "pd-hint pd-block" }, o.help) : null));
    });
  }
  function status(el, text, kind) { el.textContent = text || ""; el.className = "pd-status" + (kind ? " " + kind : ""); }
  const personPath = (id) => `${BASE}/${encodeURIComponent(id)}/data`;

  // ---------- export ----------
  async function runExport(person, options, statusEl) {
    status(statusEl, "Preparing the file…");
    const res = await call(personPath(person.id) + "/export", { method: "POST", json: { options } });
    const type = res.headers.get("Content-Type") || "";
    if (!type.includes("application/json")) {
      saveBlob(await res.blob(), filenameOf(res));
      status(statusEl, "Downloaded.", "ok");
      return true;
    }
    const { job } = await res.json();
    for (;;) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await json(`${BASE}/data/jobs/${encodeURIComponent(job)}`);
      if (st.state === "failed") throw new Error(st.error || "The export failed.");
      if (st.state === "done") {
        const file = await call(`${BASE}/data/jobs/${encodeURIComponent(job)}/file`);
        saveBlob(await file.blob(), st.filename || filenameOf(file));
        status(statusEl, "Downloaded.", "ok");
        return true;
      }
      status(statusEl, st.total ? `Preparing the file… ${Math.round((st.done / st.total) * 100)}%` : "Preparing the file…");
    }
  }

  async function exportDialog(person, cfg) {
    const body = h("div", null, h("p", { class: "pd-hint" }, "Loading…"));
    const m = modal(`Export ${person.name}'s data`, body);
    let sum;
    try { sum = await json(personPath(person.id)); } catch (e) { UI.mount(body, h("p", { class: "pd-error" }, e.message)); return; }
    const values = {};
    const st = h("div", { class: "pd-status" });
    const go = btn("Download", "pd-primary", async () => {
      go.disabled = true;
      try { await runExport(person, values, st); } catch (e) { status(st, e.message, "error"); }
      go.disabled = false;
    });
    UI.mount(body,
      h("p", null, "Everything ", h("strong", null, person.name), " has in this app, as one .zip file:"),
      countsList(sum.counts),
      checks(sum.exportOptions, values),
      h("p", { class: "pd-warn" }, sum.exportNote || "The file holds personal data and isn't encrypted — keep it somewhere safe."),
      st,
      h("div", { class: "pd-actions" }, btn("Close", "", () => m.close()), go));
  }

  // ---------- import ----------
  function importDialog(person, cfg) {
    const file = h("input", { type: "file", accept: ".zip,application/zip" });
    const st = h("div", { class: "pd-status" });
    const body = h("div", null,
      h("p", null, person ? ["Bring data from an export file into ", h("strong", null, person.name), "."]
        : "Bring a person's data from an export file (made by Export their data, here or on another install)."),
      h("label", { class: "pd-field" }, "Export file (.zip)", file), st);
    const m = modal(person ? `Import into ${person.name}` : "Import a person's data", body);
    file.addEventListener("change", async () => {
      if (!file.files.length) return;
      status(st, "Checking the file…");
      const form = new FormData();
      form.append("file", file.files[0]);
      let r;
      try { r = await json(`${BASE}/data/import`, { method: "POST", form }); }
      catch (e) { status(st, e.message, "error"); return; }
      status(st, "");
      previewStep(m, body, r, person, cfg);
    });
  }

  function previewStep(m, body, r, person, cfg) {
    const p = r.preview || {};
    const people = (cfg.people || []).slice();
    const target = h("select", null, people.map((x) => h("option", { value: x.id }, x.name)));
    target.value = (person && person.id) || p.target || (people[0] && people[0].id) || "";
    const mode = { value: "add" };
    const modeBox = h("div", { class: "pd-modes" },
      ["add", "replace"].map((v) => {
        const rb = h("input", { type: "radio", name: "pdMode", value: v, checked: v === "add" ? true : null });
        rb.addEventListener("change", () => { mode.value = v; confirmRow.hidden = v !== "replace"; });
        return h("label", { class: "pd-check" }, rb, h("span", null,
          v === "add" ? "Add to what they have" : "Replace what they have",
          h("span", { class: "pd-hint pd-block" }, v === "add" ? "What's already there is kept; the same things aren't added twice."
            : "Their current data here is deleted first, then the file is loaded.")));
      }));
    const confirm = h("input", { type: "text", autocomplete: "off", placeholder: "Type their name" });
    const confirmRow = h("label", { class: "pd-field", hidden: true }, "To replace, type the name of the person it goes to", confirm);
    const values = {};
    const st = h("div", { class: "pd-status" });
    const when = p.exportedAt ? new Date(p.exportedAt).toLocaleString() : "?";
    const go = btn("Import", "pd-primary", async () => {
      go.disabled = true;
      status(st, "Importing…");
      try {
        const res = await json(`${personPath(target.value)}/import/${encodeURIComponent(r.token)}`,
          { method: "POST", json: { mode: mode.value, confirm: confirm.value, options: values } });
        status(st, res.message || "Imported.", "ok");
        go.hidden = true;
        if (cfg.onDone) cfg.onDone();
      } catch (e) { status(st, e.message, "error"); go.disabled = false; }
    });
    UI.mount(body,
      h("div", { class: "pd-preview" },
        h("p", null, "From ", h("strong", null, (p.person && p.person.name) || "someone"), ` · ${p.app || ""} ${p.appVersion || ""}`,
          h("span", { class: "pd-hint pd-block" }, `Exported ${when}${p.exportedBy ? " by " + p.exportedBy : ""}`)),
        countsList(p.counts),
        (p.warnings || []).length ? h("ul", { class: "pd-warnings" }, p.warnings.map((w) => h("li", null, w))) : null),
      h("label", { class: "pd-field" }, "Goes to", target),
      modeBox, confirmRow,
      checks(p.options, values),
      st,
      h("div", { class: "pd-actions" }, btn("Close", "", () => m.close()), go));
  }

  // ---------- delete ----------
  async function deleteDialog(person, cfg) {
    const body = h("div", null, h("p", { class: "pd-hint" }, "Loading…"));
    const m = modal(`Delete ${person.name}'s data`, body);
    let sum;
    try { sum = await json(personPath(person.id)); } catch (e) { UI.mount(body, h("p", { class: "pd-error" }, e.message)); return; }
    const choices = {};
    const choiceEls = (sum.deleteChoices || []).map((c) => {
      choices[c.key] = c.default;
      return h("fieldset", { class: "pd-choice" }, h("legend", null, c.label),
        c.help ? h("p", { class: "pd-hint" }, c.help) : null,
        c.options.map((o) => {
          const rb = h("input", { type: "radio", name: "pdc-" + c.key, value: o.value, checked: o.value === c.default ? true : null });
          rb.addEventListener("change", () => { choices[c.key] = o.value; });
          return h("label", { class: "pd-check" }, rb, h("span", null, o.label, o.help ? h("span", { class: "pd-hint pd-block" }, o.help) : null));
        }));
    });
    let forget = false;
    const keepRb = h("input", { type: "radio", name: "pdForget", checked: true });
    const forgetRb = h("input", { type: "radio", name: "pdForget" });
    keepRb.addEventListener("change", () => { forget = false; });
    forgetRb.addEventListener("change", () => { forget = true; });
    const copyFirst = h("input", { type: "checkbox", checked: true });
    const confirm = h("input", { type: "text", autocomplete: "off", placeholder: "Type their name" });
    const st = h("div", { class: "pd-status" });
    const go = btn("Delete their data", "pd-danger", async () => {
      go.disabled = true;
      try {
        // a wrong name is caught here first, so no safety copy is downloaded for a delete that would be refused
        // (the same rule as the server's names_match, which still has the last word)
        const norm = (x) => String(x || "").normalize("NFC").split(/\s+/).filter(Boolean).join(" ").toLowerCase();
        if (norm(confirm.value) !== norm(person.name)) {
          status(st, `To delete, type ${person.name}'s name exactly.`, "error");
          go.disabled = false;
          return;
        }
        // the safety copy uses the export's own defaults (e.g. PDFs on), so it's the same file Export would give
        if (copyFirst.checked) {
          const defaults = {};
          (sum.exportOptions || []).forEach((o) => { defaults[o.key] = !!o.default; });
          await runExport(person, defaults, st);
        }
        status(st, "Deleting…");
        const res = await json(personPath(person.id) + "/delete", { method: "POST", json: { confirm: confirm.value, forget, choices } });
        status(st, res.message || "Deleted.", "ok");
        go.hidden = true;
        cancel.textContent = "Close";      // nothing left to cancel
        if (cfg.onDone) cfg.onDone();
      } catch (e) { status(st, e.message, "error"); go.disabled = false; }
    });
    const cancel = btn("Cancel", "", () => m.close());
    UI.mount(body,
      h("p", null, sum.deleteLead || "This deletes:"),
      countsList(sum.counts),
      (sum.keeps || []).length ? [h("p", null, "Stays for everyone else:"), h("ul", { class: "pd-list" }, sum.keeps.map((k) => h("li", null, k)))] : null,
      choiceEls,
      h("fieldset", { class: "pd-choice" }, h("legend", null, "And then"),
        h("label", { class: "pd-check" }, keepRb, h("span", null, "Keep them, empty",
          h("span", { class: "pd-hint pd-block" }, "They stay in the list, as if new."))),
        h("label", { class: "pd-check" }, forgetRb, h("span", null, "Forget them too",
          h("span", { class: "pd-hint pd-block" }, sum.forgetNote || "They come back as someone new if they open the app again with their Home Assistant login.")))),
      h("label", { class: "pd-check" }, copyFirst, h("span", null, "Download a copy first")),
      (sum.cannotReach || []).length ? h("p", { class: "pd-hint" }, "It can't reach: " + sum.cannotReach.join("; ") + ".") : null,
      sum.deleteNote ? h("p", { class: "pd-warn" }, sum.deleteNote) : null,
      h("label", { class: "pd-field" }, `To confirm, type ${person.name}`, confirm),
      st,
      h("div", { class: "pd-actions" }, cancel, go));
    confirm.addEventListener("input", () => { go.disabled = !confirm.value.trim(); });
    go.disabled = true;
  }

  function open(person, what, cfg = {}) {
    if (what === "export") return exportDialog(person, cfg);
    if (what === "import") return importDialog(person, cfg);
    if (what === "delete") return deleteDialog(person, cfg);
  }

  // One click anywhere closes every open Data menu. Added once, so re-drawing a people list doesn't pile up listeners.
  let closerAdded = false;
  function addCloser() {
    if (closerAdded) return;
    closerAdded = true;
    document.addEventListener("click", () => {
      document.querySelectorAll(".pd-menu").forEach((x) => {
        x.hidden = true;
        const btn = x.parentNode && x.parentNode.querySelector(".pd-menu-btn");
        if (btn) btn.setAttribute("aria-expanded", "false");
      });
    });
  }

  function menu(person, cfg = {}) {
    addCloser();
    const b = btn("⋯ Data", "pd-menu-btn", null, { "aria-haspopup": "true", "aria-expanded": "false", title: "Export, import or delete their data" });
    const list = h("div", { class: "pd-menu", hidden: true, role: "menu" },
      [["export", "Export their data"], ["import", "Import into this person"], ["delete", "Delete their data…"]].map(([k, label]) =>
        h("button", { type: "button", role: "menuitem", class: "pd-menu-item" + (k === "delete" ? " danger" : ""),
          onclick: (e) => { e.stopPropagation(); close(); open(person, k, cfg); } }, label)));
    const wrap = h("span", { class: "pd-menu-wrap" }, b, list);
    function close() { list.hidden = true; b.setAttribute("aria-expanded", "false"); }
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const opening = list.hidden;
      document.querySelectorAll(".pd-menu").forEach((x) => { x.hidden = true; });
      list.hidden = !opening;
      if (opening) {        // opens rightwards from the button; flipped when that would run off the right edge
        list.classList.remove("pd-flip", "pd-up");
        const r = list.getBoundingClientRect();
        if (r.right > document.documentElement.clientWidth - 8) list.classList.add("pd-flip");
        // opens upwards when it would end below the screen (or under a fixed bottom bar) and there's room above
        if (r.bottom > window.innerHeight - 72 && b.getBoundingClientRect().top > r.height + 8) list.classList.add("pd-up");
      }
      b.setAttribute("aria-expanded", opening ? "true" : "false");
    });
    wrap.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); b.focus(); } });
    return wrap;
  }

  function importButton(cfg = {}) {
    return btn("⬆ Import a person's data…", "pd-import-btn", () => importDialog(null, cfg));
  }

  window.PeopleData = { open, menu, importButton };
})();
