"use strict";
/* Household Docs — Admin (admins only; the server enforces it, hiding the menu item is a convenience):
   Documents folder (§5.6: status, the first-run choice with a folder browser over /share, Rescan now), People
   (access, folder name, document count and size, phones and notify services, delete their documents), Shared
   folders (§9: existing /share folders handed out read only or read and write), App settings (drawn by
   common/settings.js) and Backup. Later steps add tabs with Admin.addTab(...). */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, pageHead, fmtSize, fmtWhen } = D;
  const { mount } = UI;
  const TABS = [];
  const Admin = (window.Admin = { addTab(t) { TABS.push(t); TABS.sort((a, b) => (a.order || 0) - (b.order || 0)); },
    settingsFields: {}, settingsGroups: {}, settingsHooks: [] });    // App settings: later steps' controls (AI's Test connection)

  D.route("admin", async (page, args, current) => {
    if (!D.state.me.isAdmin) {
      mount(page, pageHead("Admin"), h("div", { class: "card", id: "adminDenied" }, h("h3", null, "Only admins can open this page"),
        h("div", { class: "hint" }, "Admin is for the people listed in the ", h("code", null, "admin_users"), " option on the app's Configuration tab.")));
      return;
    }
    let sub = args[0];
    if (!TABS.some((t) => t.id === sub)) sub = TABS[0].id;
    const tabs = h("div", { class: "admin-tabs", role: "tablist", "aria-label": "Admin sections" },
      TABS.map((t) => h("a", { role: "tab", class: t.id === sub ? "active" : "", "aria-selected": t.id === sub ? "true" : "false",
        href: "#/admin/" + t.id, dataset: { adminTab: t.id } }, t.label)));
    const body = h("div", { class: "admin-body" }, spinner());
    mount(page, pageHead("Admin"), tabs, body);
    await TABS.find((t) => t.id === sub).render(body, args.slice(1), current);
  });

  // =====================================================================
  // Documents folder
  // =====================================================================
  const SHARE_WARNING = "Every document is an ordinary file under /share. Anyone who can reach /share — Samba or NFS users, the File editor, other apps with access to Share, and anyone holding a Home Assistant backup that includes Share — can read and change everyone's documents there. The sharing people set in this app only applies inside the app.";

  function folderBrowser(onPick, start, opts = {}) {
    const body = h("div", { class: "picker" }, spinner());
    const m = openModal(opts.title || "Choose a folder in /share", body, { focus: false });
    async function show(path) {
      mount(body, spinner());
      let d;
      try { d = await api("api/admin/share-dirs?path=" + encodeURIComponent(path || "")); }
      catch (e) { if (path) return show(""); mount(body, h("div", { class: "error-text" }, e.message)); return; }
      const parts = d.rel ? d.rel.split("/") : [];
      const crumbs = h("div", { class: "crumbs small" }, h("button", { class: "link-btn", type: "button", onclick: () => show("") }, "/share"),
        parts.map((p, i) => [h("span", { class: "crumb-sep" }, "›"), i < parts.length - 1
          ? h("button", { class: "link-btn", type: "button", onclick: () => show(parts.slice(0, i + 1).join("/")) }, p) : h("span", { class: "crumb current" }, p)]));
      const rows = d.folders.map((f) => h("button", { class: "picker-row", type: "button", dataset: { folder: f.name },
        onclick: () => show((d.rel ? d.rel + "/" : "") + f.name) }, f.chat ? "💬 " : f.docs ? "📄 " : "📁 ", f.name,
        f.docs ? h("span", { class: "chip" }, "documents folder") : null, f.chat ? h("span", { class: "chip warn" }, "Household Chat's") : null,
        f.shared ? h("span", { class: "chip on" }, "shared as " + f.shared) : null,
        h("span", { class: "picker-go" }, "›")));
      const newName = h("input", { type: "text", placeholder: "New folder name (optional)", "aria-label": "New folder name", maxlength: "100" });
      mount(body, crumbs, d.note ? h("p", { class: "hint", id: "pickerSealed" }, d.note) : null,
        rows.length ? h("div", { class: "picker-list" }, rows) : (d.note ? null : h("div", { class: "empty" }, "No folders here.")),
        opts.noNew ? null : h("div", { class: "form-row" }, newName),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "pickFolder", onclick: () => {
            const extra = newName.value.trim().replace(/^\/+|\/+$/g, "");
            m.close();
            onPick(d.path + (extra ? "/" + extra : ""));
          } }, "Choose this folder")));
    }
    show(start || "");
  }

  function chooser(st, onDone) {
    const input = h("input", { type: "text", id: "docsPath", value: st.path || st.default, "aria-label": "Documents folder", spellcheck: "false", maxlength: "400" });
    const verdict = h("div", { class: "hint", id: "docsVerdict", role: "status" });
    const check = async () => {
      try {
        const r = await api("api/admin/docs-folder/inspect", { method: "POST", body: { path: input.value } });
        verdict.className = r.ok ? "hint ok" : "hint danger";
        verdict.textContent = (r.ok ? "✓ " : "✕ ") + r.message;
        return r.ok;
      } catch (e) { verdict.className = "hint danger"; verdict.textContent = e.message; return false; }
    };
    input.addEventListener("change", check);
    const browse = h("button", { class: "btn-ghost", type: "button", onclick: () => folderBrowser((p) => { input.value = p; check(); }, "") }, "Browse…");
    const use = h("button", { class: "btn-primary", type: "button", id: "useFolder", onclick: async () => {
      use.disabled = true;
      try {
        await api("api/admin/docs-folder/choose", { method: "POST", body: { path: input.value } });
        toast("Documents folder set");
        await D.refreshMe();
        if (onDone) onDone();
      } catch (e) { verdict.className = "hint danger"; verdict.textContent = e.message; }
      use.disabled = false;
    } }, "Use this folder");
    check();
    return h("div", { class: "chooser" }, h("div", { class: "form-row" }, input, browse), verdict, h("div", { class: "actions" }, use));
  }

  // the home page's first-run card for admins (SPEC §5.6)
  D.firstRunCard = function () {
    const box = h("div", { class: "card first-run", id: "firstRun" }, h("h3", null, "Where should documents be kept?"),
      h("p", null, "Everyone's documents are kept as ordinary files in a folder inside Home Assistant's /share — the default is filled in. People can already use the app; this makes the choice yours. Network storage mounted under /share works too."),
      h("p", { class: "hint warn" }, SHARE_WARNING), spinner());
    api("api/admin/docs-folder").then((st) => {
      box.lastChild.replaceWith(st.files > 0 && st.path ? h("div", null, h("p", null, "Documents are already kept in ", h("code", null, st.path), "."),
        h("button", { class: "btn-primary", type: "button", id: "useFolder", onclick: async () => {
          try { await api("api/admin/docs-folder/choose", { method: "POST", body: { path: st.path } }); toast("Documents folder confirmed"); await D.refreshMe(); D.render(); } catch (e) { fail(e); }
        } }, "Keep using this folder")) : chooser(st, () => D.render()));
    }).catch((e) => box.lastChild.replaceWith(h("div", { class: "error-text" }, e.message)));
    return box;
  };

  Admin.addTab({ id: "folder", label: "Documents folder", order: 10, render: async (box, _args, current) => {
    const st = await api("api/admin/docs-folder");
    if (!current()) return;
    const kv = (k, v) => h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, k), h("div", { class: "kv-value" }, v));
    const state = st.ok ? h("span", { class: "chip on" }, "OK") : h("span", { class: "chip warn" }, st.verdict === "parent_missing" || st.verdict === "new" ? "Not found" : st.verdict === "read_only" ? "Read-only" : "Can't be used");
    const rescan = h("button", { class: "btn-secondary", type: "button", id: "rescanNow", onclick: async () => {
      rescan.disabled = true;
      try { const r = await api("api/admin/rescan", { method: "POST" }); toast(`Rescanned: ${r.new} new, ${r.changed} changed, ${r.moved} moved, ${r.gone} gone`); D.render(); }
      catch (e) { fail(e); rescan.disabled = false; }
    } }, "Rescan now");
    mount(box,
      h("div", { class: "card", id: "folderStatus" }, h("h3", null, "Documents folder"),
        h("div", { class: "kv" }, kv("Location", h("code", null, st.path)), kv("Status", [state, st.ok ? null : h("span", { class: "hint" }, st.message)]),
          kv("Free space", st.free === null ? "—" : fmtSize(st.free)), kv("Documents", `${st.files} file${st.files === 1 ? "" : "s"}, ${fmtSize(st.size)}`),
          kv("People with a folder", st.people), kv("Last full scan", st.lastScanAt ? fmtWhen(st.lastScanAt) : "—")),
        h("div", { class: "actions" }, rescan),
        h("p", { class: "hint" }, "Files added, changed, renamed or deleted outside the app are found by a scan every few minutes (App settings), whenever a folder is opened, and by Rescan now.")),
      h("div", { class: "card warn-card" }, h("h3", null, "Who else can read these files"), h("p", null, SHARE_WARNING),
        h("p", { class: "hint" }, "To keep the folder off a Samba share, put it somewhere the Samba app doesn't share, or on its own network share with its own password. See the Documentation tab.")),
      !st.confirmed && st.files > 0 ? h("div", { class: "card", id: "confirmCard" }, h("h3", null, "Confirm the folder"),
        h("p", null, "Documents are already kept in ", h("code", null, st.path), ". To keep them somewhere else, use Move to a new location below."),
        h("div", { class: "actions left" }, h("button", { class: "btn-primary", type: "button", id: "useFolder", onclick: async () => {
          try { await api("api/admin/docs-folder/choose", { method: "POST", body: { path: st.path } }); toast("Documents folder confirmed"); await D.refreshMe(); D.render(); } catch (e) { fail(e); }
        } }, "Keep using this folder"))) : null,
      st.files === 0 ? h("div", { class: "card" }, h("h3", null, st.confirmed ? "Choose a different folder" : "Confirm or choose the folder"),
        h("p", { class: "hint" }, "Any folder inside /share (not /share itself), including network storage mounted there. A new or empty folder is set up by the app; a folder of another install, one inside Household Chat's files folder, or one that already holds other files is refused."),
        chooser(st, () => D.render())) : null,
      (st.ok && st.files > 0) || st.readOnly || st.previous ? moveCard(st) : null);
  } });

  // =====================================================================
  // Moving to a new location (§5.7): you copy, the app checks the copy and switches
  // =====================================================================
  const moveState = { job: null, path: null };
  function copyRow(label, text) {
    return h("div", { class: "path-box cmd" }, h("code", null, text),
      h("button", { class: "icon-btn", type: "button", title: "Copy", "aria-label": `Copy the ${label}`, onclick: () => navigator.clipboard && navigator.clipboard.writeText(text).then(() => toast("Copied")) }, "⧉"));
  }
  function moveCard(st) {
    const ro = st.readOnly;
    const card = h("div", { class: "card move-card", id: "moveCard" });
    const prev = st.previous;
    const target = h("input", { type: "text", id: "moveTarget", "aria-label": "New location", spellcheck: "false", maxlength: "400",
      value: moveState.path || (ro && ro.target) || UI.lsGet("docs.moveTarget") || "", placeholder: "/share/nas/household_docs" });
    const verdict = h("div", { class: "hint", id: "moveVerdict", role: "status" });
    const howBox = h("div", { class: "move-how", id: "moveHow" });
    const checkBox = h("div", { class: "move-check", id: "moveCheck" });
    let info = null;
    async function inspect() {
      const p = target.value.trim();
      UI.lsSet("docs.moveTarget", p);
      if (!p) { verdict.textContent = ""; mount(howBox); info = null; return; }
      try {
        info = await api("api/admin/docs-folder/target", { method: "POST", body: { path: p } });
        verdict.className = info.ok ? "hint ok" : "hint danger";
        verdict.textContent = (info.ok ? "✓ " : "✕ ") + info.message;
        if (info.ok) { target.value = info.path; moveState.path = info.path; }
        drawHow();
      } catch (e) { verdict.className = "hint danger"; verdict.textContent = e.message; info = null; mount(howBox); }
    }
    target.addEventListener("change", inspect);
    function drawHow() {
      if (!info || !info.ok) { mount(howBox); return; }
      const c = info.copy;
      mount(howBox,
        h("h4", null, "3 · Copy the whole folder, hidden folders included"),
        h("p", { class: "hint" }, "Copy everything in ", h("code", null, c.from), " — also ", h("code", null, ".household_docs"), ", ", h("code", null, ".trash"), " and ",
          h("code", null, ".versions"), " (without them, History and Trash don't come along) — to ", h("code", null, c.to), ". Pick one way:"),
        h("details", { class: "how", open: true }, h("summary", null, "With the Terminal & SSH app"), copyRow("rsync command", c.rsync),
          h("p", { class: "hint" }, "or, without rsync:"), copyRow("cp command", c.cp)),
        h("details", { class: "how" }, h("summary", null, "With the Samba app, from a computer"),
          h("ol", { class: "hint" }, h("li", null, "Turn on Show hidden files (Windows: View → Show → Hidden items; Mac: Cmd+Shift+.)."),
            h("li", null, "Open ", h("code", null, c.sambaFrom), " and copy everything in it."),
            h("li", null, "Paste into ", h("code", null, c.sambaTo), " (make the folder first if it isn't there)."))),
        h("details", { class: "how" }, h("summary", null, "With the File editor or a NAS tool"),
          h("p", { class: "hint" }, "Any tool that copies a whole folder with its hidden files works — for network storage, the NAS's own copy or sync tool is usually fastest. Copy ", h("code", null, c.from), " to ", h("code", null, c.to), ".")),
        h("h4", null, "4 · Check the copy"),
        h("div", { class: "actions left" },
          h("button", { class: "btn-primary", type: "button", id: "moveCheckBtn", onclick: () => runCheck(false) }, "Check copy"),
          h("button", { class: "btn-secondary", type: "button", id: "moveDeepBtn", onclick: () => runCheck(true) }, "Deep check (SHA-256, slower)")),
        checkBox);
      if (moveState.job && moveState.job.path === info.path) drawJob(moveState.job);
    }
    async function runCheck(deep) {
      mount(checkBox, spinner());
      let job;
      try { job = await api("api/admin/docs-folder/check", { method: "POST", body: { path: target.value.trim(), deep } }); }
      catch (e) { mount(checkBox, h("div", { class: "error-text" }, e.message)); return; }
      poll(job.id);
    }
    async function poll(id) {
      for (;;) {
        let job;
        try { job = await api(`api/admin/docs-folder/check/${encodeURIComponent(id)}`); } catch (e) { mount(checkBox, h("div", { class: "error-text" }, e.message)); return; }
        moveState.job = job;
        if (!document.body.contains(checkBox)) return;
        drawJob(job);
        if (job.phase === "done" || job.phase === "failed") return;
        await new Promise((r) => setTimeout(r, 700));
      }
    }
    function listOf(title, rows, count, id, fmt) {
      if (!count) return null;
      return h("details", { class: "move-list", id }, h("summary", null, `${title} (${count})`),
        h("ul", null, rows.map((x) => h("li", null, h("code", null, x.path), fmt ? fmt(x) : null))),
        count > rows.length ? h("p", { class: "hint" }, `…and ${count - rows.length} more — see the full report.`) : null);
    }
    function drawJob(job) {
      if (job.phase !== "done") {
        const what = job.phase === "failed" ? job.error : job.phase === "hashing"
          ? `Comparing contents (SHA-256): ${job.hashed} files${job.progress !== undefined ? `, ${Math.round(job.progress * 100)}%` : ""}…`
          : `Listing: ${job.listed.from} here, ${job.listed.to} in the new location…`;
        mount(checkBox, h("div", { class: job.phase === "failed" ? "error-text" : "hint", id: "moveProgress" }, what),
          job.progress !== undefined && job.phase === "hashing" ? h("progress", { max: "1", value: String(job.progress) }) : null);
        return;
      }
      const r = job.result;
      const kv = (k, a, b) => h("tr", null, h("th", null, k), h("td", null, a), h("td", null, b));
      const ok = (b) => (b ? "✓ there" : "✕ missing");
      const problems = r.problems;
      const missingHidden = Object.keys(r.hidden).filter((k) => r.hiddenFrom[k] && !r.hidden[k]);
      const notes = [];
      if (missingHidden.includes(".versions")) notes.push("No .versions folder in the copy: History (earlier versions) won't come along.");
      if (missingHidden.includes(".trash")) notes.push("No .trash folder in the copy: what's in Trash won't come along.");
      if (!r.marker.present) notes.push("No .household_docs marker in the copy — that's fine, the app writes it when it switches.");
      if (r.free !== null && r.needed > r.free) notes.push(`The new storage has ${fmtSize(r.free)} free, but ${fmtSize(r.needed)} more is needed.`);
      const switchBtn = h("button", { class: problems ? "btn-danger" : "btn-primary", type: "button", id: "moveSwitch", onclick: () => switchDialog(job) },
        problems ? "Switch anyway…" : "Switch to the new location");
      mount(checkBox,
        h("div", { class: problems ? "warn-box" : "ok-box", id: "moveVerdictBox" }, problems
          ? `${r.missingCount} missing and ${r.differentCount} different in the new location${job.deep ? "" : " (size or time)"} — copy again and check, or switch anyway.`
          : `The copy matches${job.deep ? " (contents compared with SHA-256)" : " (sizes and times)"}.` + (r.extraCount ? ` ${r.extraCount} extra item${r.extraCount === 1 ? " is" : "s are"} only in the new location.` : "")),
        h("table", { class: "move-table" }, h("tr", null, h("th"), h("th", null, "Now"), h("th", null, "New location")),
          kv("Files", String(r.from.files), String(r.to.files)), kv("Folders", String(r.from.folders), String(r.to.folders)),
          kv("Size", fmtSize(r.from.bytes), fmtSize(r.to.bytes)),
          kv("people/ · .trash · .versions", ["people", ".trash", ".versions"].map((k) => ok(r.hiddenFrom[k])).join(" · "), ["people", ".trash", ".versions"].map((k) => ok(r.hidden[k])).join(" · ")),
          kv("Marker (.household_docs)", "✓ there", r.marker.present ? (r.marker.ours ? "✓ there (this app's)" : "✕ another install's") : "— (written at the switch)"),
          kv("Free space", "", r.free === null ? "—" : fmtSize(r.free))),
        notes.length ? h("ul", { class: "hint move-notes" }, notes.map((n) => h("li", null, n))) : null,
        listOf("Missing in the new location", r.missing, r.missingCount, "moveMissing", (x) => (x.folder ? " (folder)" : ` ${fmtSize(x.size)}`)),
        listOf("Different", r.different, r.differentCount, "moveDifferent", (x) => ` — ${x.why}`),
        listOf("Only in the new location", r.extra, r.extraCount, "moveExtra", (x) => (x.folder ? " (folder)" : "")),
        h("div", { class: "actions left" }, h("a", { class: "btn-ghost btn-small", href: `api/admin/docs-folder/check/${encodeURIComponent(job.id)}/report.csv`, download: "", id: "moveReport" }, "⬇ Download full report (CSV)")),
        h("h4", null, "5 · Switch"),
        h("p", { class: "hint" }, "The app writes its marker in the new folder, notes the move in the old folder's marker (the only change made there), uses the new folder from then on — every share, tag, link, tick, favourite and earlier version stays — and turns read-only mode off. Everyone gets a notification."),
        h("div", { class: "actions left" }, switchBtn));
    }
    function switchDialog(job) {
      const r = job.result;
      const n = r.problems;
      const input = h("input", { type: "text", id: "moveConfirm", autocomplete: "off", inputmode: "numeric", "aria-label": "Confirmation" });
      const err = h("div", { class: "error-text", role: "alert" });
      const m = openModal("Switch to the new location", h("div", null,
        h("p", null, "From now on documents are kept in ", h("code", null, job.path), ". The old folder ", h("code", null, job.fromPath), " is left as it is — delete it yourself when you're happy."),
        n ? h("div", { class: "warn-box" }, `${n} file${n === 1 ? " is" : "s are"} missing or different in the new location: the app will use what's there. Type ${n} to confirm.`) : null,
        n ? h("label", { class: "field" }, `Type ${n} to switch anyway`, input) : null, err,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: n ? "btn-danger" : "btn-primary", type: "button", id: "moveSwitchGo", onclick: async (e) => {
            e.target.disabled = true;
            try {
              const out = await api("api/admin/docs-folder/switch", { method: "POST", body: { path: job.path, jobId: job.id, force: !!n, confirm: n ? input.value : undefined } });
              m.close();
              moveState.job = null; moveState.path = null; UI.lsSet("docs.moveTarget", "");
              toast(`Documents now kept in ${out.path} — ${out.scan.new} new, ${out.scan.gone} not found`, false, { ms: 7000 });
              await D.refreshMe(); D.render();
            } catch (x) { err.textContent = x.message; e.target.disabled = false; }
          } }, n ? "Switch anyway" : "Switch"))));
      if (n) setTimeout(() => input.focus(), 40);
    }
    const roSwitch = D.toggleSwitch(!!ro, async (on, el) => {
      if (on && !(await confirmDialog("Pause changes", "Everyone can still open, search and download, but nobody can change, add or delete anything — and Trash clean-up and version pruning wait — until you switch it off or switch to the new location.", "Pause changes"))) { el.checked = false; return; }
      try { await api("api/admin/docs-folder/read-only", { method: "POST", body: { on, target: target.value.trim() || null } }); toast(on ? "Changes paused" : "Changes allowed again"); await D.refreshMe(); D.render(); }
      catch (e) { fail(e); el.checked = !on; }
    }, "Pause changes while the files are copied");
    mount(card, h("h3", null, "Move to a new location"),
      h("p", { class: "hint" }, "To move the documents (for example to network storage), you copy the folder, the app checks the copy, then switches. The app never copies, moves or deletes the folder itself."),
      prev ? h("div", { class: "kv-row prev-row", id: "movePrevious" }, h("div", { class: "kv-label" }, "Previous location"),
        h("div", { class: "kv-value" }, h("code", null, prev.path), prev.exists ? " — no longer used; delete it yourself when you're happy. " : " — no longer there.",
          prev.exists ? h("button", { class: "btn-ghost btn-small", type: "button", id: "switchBack", onclick: () => { target.value = prev.path; moveState.path = prev.path; inspect(); } }, "Switch back…") : null)) : null,
      h("h4", null, "1 · Choose the new location"),
      h("div", { class: "form-row" }, target, h("button", { class: "btn-ghost", type: "button", onclick: () => folderBrowser((p) => { target.value = p; inspect(); }, "", { title: "Choose the new location" }) }, "Browse…")),
      verdict,
      h("h4", null, "2 · Pause changes (recommended)"),
      h("div", { class: "setting-row" }, h("div", null, h("div", null, "Read-only mode"),
        h("div", { class: "sub" }, ro ? `On since ${fmtWhen(ro.since)} — everyone sees “Documents are being moved”.` : "Off. Turn it on before copying, so nothing changes after the copy.")), roSwitch),
      howBox);
    if (target.value) inspect();
    return card;
  }

  // =====================================================================
  // People
  // =====================================================================
  Admin.addTab({ id: "people", label: "People", order: 20, render: async (box, args, current) => {
    let data, avail;
    try { [data, avail] = await Promise.all([api("api/admin/users"), api("api/admin/notify-services").catch(() => null)]); }
    catch (e) { mount(box, D.errorCard(e)); return; }
    if (!current()) return;
    const again = () => D.render();
    const patch = async (u, body) => {
      try { await api(`api/admin/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body }); toast("Saved"); } catch (e) { fail(e); }
      again();
    };
    const services = avail || { available: false, services: [], entities: [], error: null };
    PeoplePage.render(box, {
      people: data.users,
      cardClass: "card",
      intro: h("p", { class: "hint" }, "Everyone in Home Assistant with a login is here. New people get access straight away unless App settings say otherwise. Turning someone off hides the app from them; their folder and files stay, and stay shared. You see counts and sizes only — never anyone's documents."),
      checkAgain: async () => { try { await api("api/admin/users?refresh=1"); toast("Read from Home Assistant"); again(); } catch (e) { fail(e); } },
      person: (u) => ({
        badges: [u.you ? ["you"] : null, u.isAdmin ? ["admin", "accent"] : null, u.disabled ? ["turned off", "warn"] : null,
          u.isChild ? ["child", "accent"] : null],
        sub: [u.username ? `login ${u.username}` : null, u.folder ? `folder ${u.folder}` : "no folder yet",
          `${u.docCount} file${u.docCount === 1 ? "" : "s"}, ${fmtSize(u.docSize)}`, u.lastSeen ? `last seen ${fmtWhen(u.lastSeen)}` : null].filter(Boolean).join(" · "),
        controls: [h("label", { class: "pp-toggle" }, "Access", PeoplePage.accessSwitch(!u.disabled, (on) => patch(u, { disabled: !on }), { label: `${u.name} can use Household Docs` })),
          // Kids' space (§17.20): a simpler app — no sheets, sharing, shared folders (but Kids folders), AI or patterns
          h("label", { class: "pp-toggle", title: "Kids' space: notes and checklists, what's shared with them and Kids folders — no sheets, sharing, AI or pattern search" }, "Child",
            PeoplePage.accessSwitch(u.isChild, (on) => patch(u, { isChild: on }), { label: `${u.name} is a child` }))],
        actions: [
          u.folder ? h("button", { class: "btn-ghost btn-small", type: "button", dataset: { renameFolder: u.id }, onclick: async () => {
            const name = await D.askName(`Rename ${u.name}'s folder`, "Folder name", u.folder, "Rename");
            if (name && name !== u.folder) patch(u, { folder: name });
          } }, "Rename folder") : null,
          u.folder && u.docCount ? h("button", { class: "btn-danger btn-small", type: "button", dataset: { deleteDocs: u.id }, onclick: () => deleteDocsDialog(u, again) }, "Delete their documents…") : null,
          u.isChild ? h("button", { class: "btn-ghost btn-small", type: "button", dataset: { parents: u.id }, onclick: () => parentsDialog(u, data.users, again) },
            u.parents && u.parents.length ? `Parents can view (${u.parents.length})…` : "Let parents view…") : null,
        ],
        blocks: h("div", { class: "pp-line" }, h("span", { class: "pp-label" }, "Phone"), h("div", { class: "pp-chips" }, PeoplePage.phoneChips(u, true))),
      }),
      notify: {
        mode: "dialog", api, services, openModal: (t, c, o) => openModal(t, c, o),
        loadServices: () => api("api/admin/notify-services"),
        toast: (m, err) => toast(m, !!err), fail,
        path: (u) => `api/admin/users/${encodeURIComponent(u.id)}/notify`,
        testPath: (u) => `api/admin/users/${encodeURIComponent(u.id)}/notify/test`,
        texts: { intro: (u) => `${u.name} is told when something is shared with them (they can turn that off in their Settings).`, none: () => "none" },
        retry: again,
      },
      empty: "Nobody has opened the app yet.",
    });
  } });

  // §17.20: who may view a child's My docs (Can view on everything in it)
  function parentsDialog(u, users, again) {
    const chosen = new Set(u.parents || []);
    const others = users.filter((x) => x.id !== u.id && !x.isChild);
    const err = h("div", { class: "error-text", role: "alert" });
    const m = openModal(`Who may view ${u.name}'s docs`, h("div", null,
      h("p", { class: "hint" }, `The people ticked here can open, search and download everything in ${u.name}'s My docs (not change it). They find it under 🧒 Kids' docs.`),
      others.length ? h("div", { class: "pick-list", id: "parentsList" }, others.map((x) => h("label", { class: "mini-toggle" },
        h("input", { type: "checkbox", checked: chosen.has(x.id), dataset: { parent: x.id }, onchange: (e) => { if (e.target.checked) chosen.add(x.id); else chosen.delete(x.id); } }), x.name)))
        : h("div", { class: "empty" }, "Nobody else yet."),
      err,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "parentsSave", onclick: async () => {
          try { await api(`api/admin/users/${encodeURIComponent(u.id)}`, { method: "PATCH", body: { parents: Array.from(chosen) } }); m.close(); toast("Saved"); again(); }
          catch (e) { err.textContent = e.message; }
        } }, "Save"))), { focus: false });
  }

  function deleteDocsDialog(u, again) {
    const input = h("input", { type: "text", "aria-label": "Folder name", id: "confirmFolder", autocomplete: "off" });
    const err = h("div", { class: "error-text", role: "alert" });
    const m = openModal(`Delete ${u.name}'s documents`, h("div", null,
      h("p", null, `${u.docCount} file${u.docCount === 1 ? "" : "s"} (${fmtSize(u.docSize)}) move to ${u.name}'s Trash, where they can still be restored until it's emptied.`),
      h("label", { class: "field" }, `Type the folder name — ${u.folder} — to confirm`, input), err,
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-danger", type: "button", id: "confirmDelete", onclick: async () => {
          try { await api(`api/admin/users/${encodeURIComponent(u.id)}/docs/delete`, { method: "POST", body: { confirm: input.value } }); m.close(); toast("Moved to their Trash"); again(); }
          catch (e) { err.textContent = e.message; }
        } }, "Delete"))));
  }

  // =====================================================================
  // Shared folders (§9.1)
  // =====================================================================
  const MODES = [["none", "No access"], ["ro", "Read only"], ["rw", "Read and write"]];
  function accessTable(people, access, everyone) {
    const state = Object.assign({}, access, { "*": everyone || "none" });
    const sel = (uid, label) => {
      const s = h("select", { "aria-label": `Access for ${label}`, dataset: { access: uid }, value: state[uid] || "none" }, MODES.map(([v, l]) => h("option", { value: v }, l)));
      s.addEventListener("change", () => { state[uid] = s.value; });
      return s;
    };
    const el = h("div", { class: "access-table" },
      h("div", { class: "access-row everyone" }, h("span", null, "🏡 Everyone", h("span", { class: "hint" }, " — people added later too")), sel("*", "Everyone")),
      people.map((p) => h("div", { class: "access-row" }, h("span", null, p.name, p.disabled ? h("span", { class: "chip warn" }, "turned off") : null), sel(p.id, p.name))));
    return { el, value: () => state };
  }
  function folderDialog(people, f, onDone) {
    const editing = !!f;
    const path = h("input", { type: "text", id: "sfPathInput", value: f ? f.path : "/share/", "aria-label": "Folder", spellcheck: "false", maxlength: "400", disabled: editing });
    const label = h("input", { type: "text", id: "sfLabel", value: f ? f.label : "", "aria-label": "Name", maxlength: "80", placeholder: "House papers" });
    const verdict = h("div", { class: "hint", id: "sfVerdict", role: "status" });
    const err = h("div", { class: "error-text", role: "alert" });
    const acc = accessTable(people, f ? f.access : {}, f ? f.everyone : "none");
    const kidsBox = h("input", { type: "checkbox", id: "sfKids", checked: !!(f && f.kids) });
    const check = async () => {
      if (editing) return;
      try {
        const r = await api("api/admin/shared-folders/inspect", { method: "POST", body: { path: path.value } });
        verdict.className = r.ok ? "hint ok" : "hint danger";
        verdict.textContent = (r.ok ? "✓ " : "✕ ") + r.message;
        if (r.ok && !label.value.trim()) label.value = (r.path || "").split("/").pop();
      } catch (e) { verdict.className = "hint danger"; verdict.textContent = e.message; }
    };
    path.addEventListener("change", check);
    const browse = editing ? null : h("button", { class: "btn-ghost", type: "button", onclick: () => folderBrowser((p) => { path.value = p; check(); }, (path.value || "").replace(/^\/share\/?/, ""), { noNew: true, title: "Choose a folder to share" }) }, "Browse…");
    const m = openModal(editing ? `Edit “${f.label}”` : "Add a shared folder", h("div", null,
      h("label", { class: "field" }, "Folder in /share", h("div", { class: "form-row" }, path, browse)), verdict,
      h("label", { class: "field" }, "Name people see", label),
      h("div", { class: "field" }, "Who can use it", acc.el),
      h("label", { class: "mini-toggle" }, kidsBox, "Kids folder — children with access see it (they see no other shared folders)"), err,
      h("p", { class: "hint" }, "Read only: open, search, preview, download, copy into My docs. Read and write adds making, uploading, renaming, moving and deleting (to the folder's own hidden .trash). Nothing on disk changes when you stop sharing."),
      h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
        h("button", { class: "btn-primary", type: "button", id: "sfSave", onclick: async () => {
          err.textContent = "";
          try {
            if (editing) await api(`api/admin/shared-folders/${encodeURIComponent(f.id)}`, { method: "PATCH", body: { label: label.value, access: acc.value(), kids: kidsBox.checked } });
            else await api("api/admin/shared-folders", { method: "POST", body: { path: path.value, label: label.value, access: acc.value(), kids: kidsBox.checked } });
            m.close();
            toast(editing ? "Saved" : "Shared — the folder is being indexed");
            await D.refreshMe();
            onDone();
          } catch (e) { err.textContent = e.message; }
        } }, editing ? "Save" : "Share this folder"))), { wide: true, focus: false });
    if (!editing) check();
  }
  Admin.addTab({ id: "folders", label: "Shared folders", order: 15, render: async (box, _args, current) => {
    const d = await api("api/admin/shared-folders");
    if (!current()) return;
    const again = () => D.render();
    const names = Object.fromEntries(d.people.map((p) => [p.id, p.name]));
    const summary = (f) => {
      const parts = [];
      if (f.everyone !== "none") parts.push(`Everyone: ${f.everyone === "rw" ? "read and write" : "read only"}`);
      const ro = Object.keys(f.access).filter((u) => f.access[u] === "ro").map((u) => names[u] || "someone");
      const rw = Object.keys(f.access).filter((u) => f.access[u] === "rw").map((u) => names[u] || "someone");
      if (rw.length) parts.push("Read and write: " + rw.join(", "));
      if (ro.length) parts.push("Read only: " + ro.join(", "));
      return parts.join(" · ") || "Nobody yet";
    };
    const cards = d.folders.map((f) => h("div", { class: "card shared-folder-card", dataset: { root: f.id } },
      h("div", { class: "card-head" }, h("h3", null, f.exists ? "🗂️ " : "⚠ ", f.label, f.kids ? h("span", { class: "chip on" }, "Kids folder") : null), h("code", null, f.path)),
      f.exists ? null : h("div", { class: "warn-box" }, f.problem || "Not found"),
      h("div", { class: "kv" },
        h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Access"), h("div", { class: "kv-value" }, summary(f))),
        h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Files"), h("div", { class: "kv-value" }, `${f.files} file${f.files === 1 ? "" : "s"}, ${fmtSize(f.size)}`)),
        h("div", { class: "kv-row" }, h("div", { class: "kv-label" }, "Last scan"), h("div", { class: "kv-value" }, f.lastScanAt ? `${fmtWhen(f.lastScanAt)} (${f.lastScanMs} ms)` : "—"))),
      h("div", { class: "actions" },
        h("button", { class: "btn-secondary btn-small", type: "button", dataset: { edit: f.id }, onclick: () => folderDialog(d.people, f, again) }, "Edit"),
        h("button", { class: "btn-ghost btn-small", type: "button", dataset: { rescan: f.id }, onclick: async (e) => {
          e.target.disabled = true;
          try { const r = await api(`api/admin/shared-folders/${encodeURIComponent(f.id)}/rescan`, { method: "POST" }); toast(r.ok ? `Rescanned: ${r.scan.new} new, ${r.scan.changed} changed, ${r.scan.gone} gone` : "Couldn't scan it — is it there?", !r.ok); again(); }
          catch (x) { fail(x); e.target.disabled = false; }
        } }, "Rescan now"),
        h("button", { class: "btn-danger btn-small", type: "button", dataset: { stop: f.id }, onclick: async () => {
          if (!(await confirmDialog("Stop sharing", `Stop sharing “${f.label}”? People lose access in the app. Nothing on disk changes — ${f.path} stays exactly as it is (its hidden .trash and .versions too).`, "Stop sharing", true))) return;
          try { await api(`api/admin/shared-folders/${encodeURIComponent(f.id)}`, { method: "DELETE" }); toast("Stopped sharing"); await D.refreshMe(); again(); } catch (x) { fail(x); }
        } }, "Stop sharing"))));
    mount(box,
      h("div", { class: "card" }, h("h3", null, "Shared folders"),
        h("p", { class: "hint" }, "Hand out an existing folder in /share — for example /share/Documents/House — to chosen people, read only or read and write. Not allowed: /share itself, hidden folders, the documents folder (or anything inside or around it), Household Chat's files folder, and folders inside or around another shared folder."),
        h("div", { class: "actions" }, h("button", { class: "btn-primary", type: "button", id: "addSharedFolder", onclick: () => folderDialog(d.people, null, again) }, "➕ Add shared folder"))),
      cards.length ? cards : h("div", { class: "empty" }, "No shared folders yet."));
  } });

  // =====================================================================
  // App settings
  // =====================================================================
  Admin.addTab({ id: "settings", label: "App settings", order: 30, render: async (box, _a, current) => {
    const data = await api("api/admin/settings");
    if (!current()) return;
    await SettingsPage.render(box, {
      data,
      load: () => api("api/admin/settings"),
      save: (body) => api("api/admin/settings", { method: "PUT", body }),
      classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
      fields: Admin.settingsFields, groups: Admin.settingsGroups,
      onChange: (key, value, page) => Admin.settingsHooks.forEach((fn) => fn(key, page)),
      afterDraw: (page) => Admin.settingsHooks.forEach((fn) => fn(null, page)),
      afterSave: async () => { await D.refreshMe(); return "Settings saved"; },
      toast: (msg, isError) => toast(msg, !!isError),
    });
    // Connected apps (APP_MESSAGES_SPEC §5): read only, drawn by common/connected-apps.js
    const apps = h("div", { class: "connected-apps-wrap", id: "connectedApps" });
    box.appendChild(apps);
    api("api/admin/connected-apps").then((d) => mount(apps, ConnectedApps.card(d, { h, when: D.fmtFull }))).catch(() => {});
  } });

  // =====================================================================
  // Backup and restore
  // =====================================================================
  Admin.addTab({ id: "backup", label: "Backup", order: 40, render: async (box, _a, current) => {
    const s = await api("api/admin/settings");
    if (!current()) return;
    const withFiles = s.values.backup_files;
    const sizeNote = h("span", { class: "hint", id: "backupSize" }, "Working out the size…");
    api("api/admin/backup/size").then((z) => {
      sizeNote.textContent = z.error ? z.error : (z.includeFiles ? `About ${fmtSize(z.total)}: the database (${fmtSize(z.database)}) and ${z.files} file${z.files === 1 ? "" : "s"} (${fmtSize(z.filesSize)}).`
        : `About ${fmtSize(z.database)} (the database).`);
    }).catch(() => { sizeNote.textContent = ""; });
    const file = h("input", { type: "file", accept: ".zip,application/zip", "aria-label": "Backup file", id: "restoreFile" });
    const replace = h("input", { type: "checkbox", id: "replaceFiles" });
    const err = h("div", { class: "error-text", role: "alert" });
    const btn = h("button", { class: "btn-danger", type: "button", id: "restoreBtn", onclick: async () => {
      err.textContent = "";
      if (!file.files.length) { err.textContent = "Choose a backup .zip first."; return; }
      if (!(await confirmDialog("Restore", "This REPLACES the app's database — sharing, the index, favourites, settings — with the backup's. The documents folder is re-scanned afterwards. There is no undo.", "Restore", true))) return;
      btn.disabled = true;
      try {
        const r = await api("api/admin/restore" + (replace.checked ? "?replaceFiles=true" : ""), { method: "POST", rawBody: file.files[0], headers: { "Content-Type": "application/zip" } });
        toast(`Restored${r.files ? ` (${r.files} files)` : ""}`);
        file.value = "";
      } catch (e) { err.textContent = e.message; }
      btn.disabled = false;
    } }, "Restore");
    mount(box,
      h("div", { class: "card" }, h("h3", null, "Backup"),
        h("p", { class: "hint" }, withFiles ? "A zip with the database and every file in the documents folder (people's folders, Trash and History). It can be large."
          : "A zip with the database: who may see what, the index, favourites, checklist ticks and settings. The documents themselves are files in /share — in Home Assistant's backups when Share is ticked — or turn on “Backup includes the documents themselves” in App settings."),
        h("div", { class: "form-row" }, h("a", { class: "btn-primary", href: "api/admin/backup", download: "", id: "downloadBackup" }, "Download backup (.zip)"), sizeNote)),
      h("div", { class: "card" }, h("h3", null, "Restore"),
        h("div", { class: "warn-box" }, "Restoring replaces everything in the app's database with the backup's. Files in the backup are put back only into an empty documents folder, unless you tick Replace files."),
        h("div", { class: "form-row" }, file), h("label", { class: "mini-toggle" }, replace, "Replace files in the documents folder with the backup's"),
        err, h("div", { class: "actions" }, btn)));
  } });

  Object.assign(Admin, { folderBrowser });
})();
