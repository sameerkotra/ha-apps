"use strict";
/* Household Docs — staying up to date (SPEC §17.4, §17.8, §17.9): 🕑 Activity (filters, grouped edits, Load more),
   ⋯ → Follow (and the shared folder's head), what you follow, Settings → follow notifications and quiet hours,
   ⋯ → Show in Home Assistant… (and the "on the dashboard" notice everyone who can see the item gets), and the
   storage report (yours, and Admin → Storage). Bars are drawn in the page (CSS widths set through the DOM, theme
   colours), never as pictures; everything from the server goes in with textContent. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, pageHead, fmtSize, fmtWhen, fmtFull, go } = D;
  const { mount } = UI;
  const can = (it, role) => ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[it.role] || 0) >= ({ viewer: 1, editor: 2, manager: 3, owner: 4 }[role] || 9);
  const readOnlyMode = () => !!(D.state.me && D.state.me.readOnly);
  const extras = () => (D.state.me && D.state.me.extras) || {};

  // =====================================================================
  // 🕑 Activity
  // =====================================================================
  D.addSpace({ id: "activity", icon: "🕑", label: "Activity", order: 55, hash: "#/activity" });

  const VERB = { created: "added", edited: "edited", renamed: "renamed", moved: "moved", deleted: "deleted", restored: "restored", shared: "shared" };
  function quote(s) { return `“${s}”`; }
  function sentence(e) {
    // [who, " edited ", <the item, a link>, " 4 times"] — the item's name is the link
    const who = e.outside ? "Someone outside the app" : (e.actorName || "Someone");
    const it = e.item;
    const link = itemLink(it, D.titleOf(Object.assign({}, it, { document: it.document })));
    if (e.groupCount) return [`${who} ${VERB[e.action]} ${e.groupCount} items`];
    switch (e.action) {
      case "edited": return [`${who} edited `, link, e.count > 1 ? ` ${e.count} times` : ""];
      case "renamed": return e.detail && e.detail.from ? [`${who} renamed ${quote(e.detail.from)} to `, link] : [`${who} renamed something to `, link];
      case "moved": return [`${who} moved `, link, e.detail && e.detail.to ? ` to ${e.detail.to}` : e.detail && e.detail.to === "" ? " to the top" : ""];
      case "shared": return [`${who} shared `, link, ` with ${e.sharedWith === "you" ? "you" : (e.sharedWith || "someone")}`];
      case "created": return [`${who} added `, link];
      case "filed": {                                   // §17.18: a filing rule renamed and/or moved an arrived file
        const d = e.detail || {};
        const renamed = d.from && d.to && d.from !== d.to;
        return [`${e.actorId === D.state.me.id ? "Your" : (e.actorName || "Someone") + "'s"} filing rule filed `, link,
          renamed ? ` (was ${quote(d.from)})` : "", d.toFolder ? ` in ${d.toFolder}` : ""];
      }
      default: return [`${who} ${VERB[e.action] || e.action} `, link];
    }
  }
  const ICON = { created: "➕", edited: "✏️", renamed: "🏷", moved: "📂", deleted: "🗑", restored: "♻️", shared: "👥", filed: "🗂" };
  function dayLabel(iso) {
    const d = new Date(iso), now = new Date();
    if (d.toDateString() === now.toDateString()) return "Today";
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return "Yesterday";
    return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
  }
  function openable(it) { return !it.inTrash && !it.gone; }
  function itemLink(it, label) {
    const text = label || it.name;
    if (!openable(it)) return h("span", { class: "act-name gone", title: it.inTrash ? "In Trash" : "Not there any more" }, text);
    if (it.kind === "folder" || it.document) return h("a", { class: "act-name", href: (it.kind === "folder" ? "#/folder/" : "#/doc/") + encodeURIComponent(it.id) }, text);
    return h("button", { class: "link-btn act-name", type: "button", onclick: () => D.openItem(it) }, text);
  }
  function entryRow(e) {
    const it = e.item;
    const more = e.items && e.items.length > 1 ? h("details", { class: "act-more" }, h("summary", null, "Show them"),
      h("ul", null, e.items.map((x) => h("li", null, itemLink(x))))) : null;
    return h("div", { class: "act-row", dataset: { action: e.action } },
      h("span", { class: "act-icon", "aria-hidden": "true" }, ICON[e.action] || "•"),
      h("div", { class: "act-text" },
        h("div", null, sentence(e)),
        h("div", { class: "hint" }, [e.location, fmtWhen(e.at), e.outside ? "found by the app's scan" : null, it.inTrash ? "in Trash" : null].filter(Boolean).join(" · ")),
        more),
      e.undo ? h("button", { class: "btn-ghost btn-small act-undo", type: "button", dataset: { undo: e.undo }, title: "Put it back where it arrived, under its old name (for 7 days)",
        onclick: async (ev) => {
          ev.target.disabled = true;
          try { await api(`api/filing/${encodeURIComponent(e.undo)}/undo`, { method: "POST" }); toast("Put back"); D.render(); }
          catch (x) { fail(x); ev.target.disabled = false; }
        } }, "Undo") : null);
  }

  async function placeOptions() {
    const me = D.state.me;
    return [["", "Everywhere"], ["mine", "My docs"], ["shared", "Shared with me"], ["folders", "Shared folders"]]
      .concat((me.sharedFolders || []).filter((f) => f.exists).map((f) => ["root:" + f.rootId, "📁 " + f.label]));
  }

  D.route("activity", async (page, args, current) => {
    mount(page, pageHead("Activity"), spinner());
    const [people, places] = await Promise.all([D.people().catch(() => []), placeOptions()]);
    if (!current()) return;
    const state = { person: "", place: args[0] || "", type: "" };
    const sel = (id, label, options, key) => {
      const s = h("select", { id, "aria-label": label }, options.map(([v, t]) => h("option", { value: v }, t)));
      s.value = state[key];
      s.addEventListener("change", () => { state[key] = s.value; load(); });
      return h("label", { class: "field compact" }, label, s);
    };
    const filters = h("div", { class: "act-filters" },
      sel("actPerson", "Who", [["", "Everyone"], ["others", "Everyone but me"], ["me", "Me"], ["outside", "Outside the app"]]
        .concat(people.filter((p) => !p.you).map((p) => [p.id, p.name])), "person"),
      sel("actPlace", "Where", places, "place"),
      sel("actType", "Type", [["", "Everything"], ["note", "Notes"], ["checklist", "Checklists"], ["sheet", "Sheets"], ["folder", "Folders"], ["file", "Files"]], "type"));
    const list = h("div", { class: "act-list", id: "activityList" });
    const moreBtn = h("button", { class: "btn-secondary", type: "button", id: "actMore", hidden: true }, "Load more");
    const following = h("div", { class: "card", id: "followingCard" });
    let next = null, lastDay = null;
    async function load(append) {
      if (!append) { mount(list, spinner()); lastDay = null; next = null; }
      const q = new URLSearchParams();
      if (state.person) q.set("person", state.person);
      if (state.place) q.set("place", state.place);
      if (state.type) q.set("type", state.type);
      if (append && next) q.set("before", next);
      let data;
      try { data = await api("api/activity?" + q.toString()); } catch (e) { mount(list, D.errorCard(e, () => load())); return; }
      if (!append) mount(list);
      if (!data.items.length && !append) mount(list, h("div", { class: "empty" }, `Nothing changed here in the last ${data.days} days.`));
      data.items.forEach((e) => {
        const day = dayLabel(e.at);
        if (day !== lastDay) { list.appendChild(h("h3", { class: "act-day" }, day)); lastDay = day; }
        list.appendChild(entryRow(e));
      });
      next = data.next;
      moreBtn.hidden = !data.more;
    }
    moreBtn.addEventListener("click", () => load(true));
    mount(page, pageHead("Activity"),
      h("p", { class: "hint" }, `What changed in your documents, what's shared with you and your shared folders — only things you can open. Follow a document or folder (⋯ → Follow) to get a phone notification.`),
      h("div", { class: "card" }, filters, list, h("div", { class: "actions" }, moreBtn)), following);
    load();
    drawFollowing(following);
  });

  async function drawFollowing(box) {
    let data;
    try { data = await api("api/follows"); } catch (e) { mount(box, h("div", { class: "error-text" }, e.message)); return; }
    const rows = data.follows.map((f) => h("div", { class: "item-row", dataset: { follow: f.target } },
      h("a", { class: "row-main", href: f.kind === "folder" ? D.folderHash(f.target) : "#/doc/" + encodeURIComponent(f.target) },
        h("span", { class: "row-icon", "aria-hidden": "true" }, f.kind === "folder" ? "📁" : D.kindOf(f).icon),
        h("span", { class: "row-text" }, h("span", { class: "row-name" }, f.name), h("span", { class: "row-meta" }, `since ${fmtWhen(f.since)}`))),
      h("button", { class: "btn-ghost btn-small", type: "button", onclick: async () => {
        try { await unfollow(f.target); drawFollowing(box); } catch (e) { fail(e); }
      } }, "Unfollow")));
    mount(box, h("h3", null, "You follow"),
      rows.length ? h("div", { class: "item-list" }, rows) : h("div", { class: "hint" }, "Nothing yet. ⋯ → Follow on a document or folder sends you a phone notification when it changes."),
      D.state.me.notifyLinked ? null : h("p", { class: "hint warn" }, "No phone is linked to you in Home Assistant yet (Settings → People → you → Track device), so follow notifications can't reach you."));
  }

  // ---------- follow / unfollow ----------
  function isFollowing(ref) { return ((D.state.me && D.state.me.follows) || []).includes(ref); }
  async function follow(ref) {
    await api("api/follows", { method: "POST", body: { target: ref } });
    D.state.me.follows = (D.state.me.follows || []).concat([ref]);
  }
  async function unfollow(ref) {
    await api("api/follows/" + encodeURIComponent(ref), { method: "DELETE" });
    D.state.me.follows = (D.state.me.follows || []).filter((x) => x !== ref);
  }
  async function toggleFollow(ref, name) {
    try {
      if (isFollowing(ref)) { await unfollow(ref); toast(`You no longer follow ${name}`); }
      else {
        await follow(ref);
        toast(D.state.me.notifyLinked ? `Following ${name}: a phone notification when it changes` : `Following ${name} — link a phone in Home Assistant to get notifications`);
      }
      if (D.state.route === "folder" || D.state.route === "doc") D.render();
    } catch (e) { fail(e); }
  }
  D.addAction({ id: "follow", order: 13, icon: "🔔", label: (it) => (isFollowing(it.id) ? "Unfollow" : "Follow"),
    show: (it) => !it.inTrash && !it.isRoot, run: (it) => toggleFollow(it.id, it.name) });
  D.rootActions.push({ id: "follow", icon: "🔔", label: (f) => (isFollowing(f.id) ? "Unfollow" : "Follow"), run: (f) => toggleFollow(f.id, f.name) });

  // ---------- Settings → You ----------
  D.settingsRows.push((me, save, row) => {
    const from = h("input", { type: "time", id: "quietFrom", value: me.prefs.quietFrom || "22:00", "aria-label": "Quiet from" });
    const to = h("input", { type: "time", id: "quietTo", value: me.prefs.quietTo || "07:00", "aria-label": "Quiet until" });
    const saveQuiet = () => { if (from.value && to.value) save({ quietFrom: from.value, quietTo: to.value }); };
    from.addEventListener("change", saveQuiet);
    to.addEventListener("change", saveQuiet);
    return [
      row("Notify me about what I follow", "⋯ → Follow on a document or folder: at most one notification per item every 15 minutes, never for your own changes.",
        D.toggleSwitch(me.prefs.notifyFollows !== false, (v) => save({ notifyFollows: v }), "Notify me about what I follow")),
      row("Quiet hours", `No follow notifications between these times (${(me.app && me.app.timeZone) || "Home Assistant's"} time); what happened is told afterwards. The same two times: no quiet hours.`,
        h("span", { class: "time-pair" }, from, h("span", { class: "hint" }, "–"), to)),
    ];
  });

  // =====================================================================
  // Show in Home Assistant (§17.9)
  // =====================================================================
  const SENSOR_KINDS = ["checklist", "sheet", "folder"];
  async function sensorDialog(ref, item) {
    const body = h("div", { class: "sensor-dialog" }, spinner());
    const m = openModal("Show in Home Assistant", body, { wide: false });
    let info;
    try { info = await api(`api/nodes/${encodeURIComponent(ref)}/ha-sensor`); } catch (e) { mount(body, h("div", { class: "error-text" }, e.message)); return; }
    const s = info.sensor;
    const name = h("input", { type: "text", id: "sensorName", maxlength: "80", value: s ? s.name || "" : D.titleOf(item), "aria-label": "Name on the dashboard" });
    const cell = h("input", { type: "text", id: "sensorCell", maxlength: "60", value: s ? s.cell || "" : "", placeholder: "Sheet1!B4 or Sheet1!B2:B9", "aria-label": "Cell or range" });
    const unit = h("input", { type: "text", id: "sensorUnit", maxlength: "20", value: s ? s.unit || "" : "", placeholder: "€, kWh, items …", "aria-label": "Unit" });
    const hide = h("input", { type: "checkbox", id: "sensorHide", checked: !!(s && s.hideItems) });
    const err = h("div", { class: "error-text", role: "alert" });
    const what = { checklist: "how many items are still open (and the first 10 of them)", sheet: "a cell's value (a range: the sum of its numbers)", folder: "how many files it holds, and the newest one" }[info.kind];
    const fields = [h("label", { class: "field" }, "Name on the dashboard", name)];
    if (info.kind === "sheet") fields.push(h("label", { class: "field" }, "Cell or range", cell), h("label", { class: "field" }, "Unit (optional)", unit));
    if (info.kind === "checklist") fields.push(h("label", { class: "mini-toggle" }, hide, "Hide item text (only the counts)"));
    const send = async (method, payload) => {
      err.textContent = "";
      try {
        await api(`api/nodes/${encodeURIComponent(ref)}/ha-sensor`, { method, body: payload });
        m.close();
        toast(method === "DELETE" ? "Removed from Home Assistant" : "Shown in Home Assistant");
        D.render();
      } catch (e) { err.textContent = e.message; }
    };
    mount(body,
      h("p", null, `A sensor in Home Assistant with ${what}, updated whenever it changes. Everyone who can see this item is told it's on the dashboard.`),
      s ? h("p", { class: "chip ok-chip" }, "📊 On the dashboard as ", h("code", null, s.entityId)) : h("p", { class: "hint" }, "It will be ", h("code", null, info.suggested), " (or similar)."),
      !info.enabled ? h("p", { class: "hint warn" }, "An admin has turned this off (Admin → App settings).") : null,
      info.canChange ? fields : h("p", { class: "hint" }, "Only people who can edit it can change this."),
      h("p", { class: "hint" }, "Sensor values are kept in Home Assistant's history. To keep a private value out of it, see “Home Assistant sensors” in the app's documentation (a recorder exclusion)."),
      err,
      h("div", { class: "actions" },
        s && info.canChange ? h("button", { class: "btn-danger", type: "button", id: "sensorOff", onclick: () => send("DELETE") }, "Turn off") : null,
        h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Close"),
        info.canChange ? h("button", { class: "btn-primary", type: "button", id: "sensorSave", onclick: () => send("PUT", { name: name.value, cell: cell.value, unit: unit.value, hideItems: hide.checked }) }, s ? "Save" : "Show in Home Assistant") : null));
  }
  D.addAction({ id: "ha-sensor", order: 78, icon: "📊", label: (it) => (it.haSensor ? "In Home Assistant…" : "Show in Home Assistant…"),
    show: (it) => SENSOR_KINDS.includes(it.kind) && !it.inTrash && !it.isRoot && (it.haSensor || (extras().haSensors && can(it, "editor") && !readOnlyMode())),
    run: (it) => sensorDialog(it.id, it) });
  D.rootActions.push({ id: "ha-sensor", icon: "📊", label: (f) => (f.haSensor ? "In Home Assistant…" : "Show in Home Assistant…"),
    show: (f) => f.haSensor || (extras().haSensors && f.role === "editor"), run: (f) => sensorDialog(f.id, f) });
  D.headExtras = D.headExtras || [];
  D.headExtras.push((it) => (it && it.haSensor ? h("div", { class: "head-note", id: "haSensorNote" }, "📊 On the Home Assistant dashboard as ", h("code", null, it.haSensor),
    " — ", h("button", { class: "link-btn", type: "button", onclick: () => sensorDialog(it.id, it) }, "details")) : null));

  // =====================================================================
  // Storage report (§17.8)
  // =====================================================================
  function bars(rows, opts) {
    const max = Math.max(1, ...rows.map((r) => opts.value(r) || 0));
    return h("div", { class: "bars", role: "img", "aria-label": opts.label },
      rows.map((r, i) => {
        const fill = h("span", { class: "bar-fill s" + (opts.same ? 0 : i % 8) + (r.estimated ? " est" : "") });
        fill.style.width = Math.max(opts.value(r) ? 1.5 : 0, Math.round((opts.value(r) || 0) / max * 1000) / 10) + "%";
        return h("div", { class: "bar-row" }, h("span", { class: "bar-label" }, opts.name(r)), h("span", { class: "bar-track" }, fill),
          h("span", { class: "bar-value" }, opts.text(r)));
      }));
  }
  function columns(rows, opts) {
    const max = Math.max(1, ...rows.map((r) => opts.value(r) || 0));
    return h("div", { class: "cols", role: "img", "aria-label": opts.label },
      rows.map((r) => {
        const fill = h("span", { class: "col-fill" + (r.estimated ? " est" : "") + (opts.value(r) === null ? " none" : "") });
        fill.style.height = Math.max(opts.value(r) ? 2 : 0, Math.round((opts.value(r) || 0) / max * 100)) + "%";
        return h("div", { class: "col", title: opts.title(r) }, h("span", { class: "col-track" }, fill), h("span", { class: "col-label" }, opts.name(r)));
      }));
  }
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function monthName(m) { return MONTHS[Number(m.slice(5, 7)) - 1] || m; }
  function growthChart(g) {
    return [columns(g, { label: "Size at the end of each month", value: (r) => r.bytes, name: (r) => monthName(r.month),
      title: (r) => `${r.month}: ${r.bytes === null ? "no record" : fmtSize(r.bytes) + ` in ${r.files} files`}${r.estimated ? " (estimated)" : ""}` }),
    g.some((r) => r.estimated) ? h("p", { class: "hint" }, "Lighter bars are estimated from when the app first saw each file still there.") : null];
  }
  function tiles(r) {
    const q = r.quota;
    const pct = q ? Math.min(100, Math.round(r.bytes / q.bytes * 100)) : null;
    const qbar = q ? h("span", { class: "quota-bar" + (pct >= 90 ? " full" : "") }, h("span", { class: "quota-fill" })) : null;
    if (qbar) qbar.firstChild.style.width = pct + "%";
    return h("div", { class: "usage-tiles" },
      h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "Size"), h("div", { class: "usage-big", id: "repSize" }, fmtSize(r.bytes)),
        q ? [qbar, h("div", { class: "hint" }, `${pct}% of the ${q.gb} GB limit`)] : null),
      h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "Files"), h("div", { class: "usage-big", id: "repFiles" }, String(r.files)), h("div", { class: "hint" }, `${r.folders} folder${r.folders === 1 ? "" : "s"}`)),
      h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "History"), h("div", { class: "usage-big" }, fmtSize(r.versions.bytes)), h("div", { class: "hint" }, `${r.versions.count} earlier cop${r.versions.count === 1 ? "y" : "ies"} (.versions)`)),
      h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "Trash"), h("div", { class: "usage-big" }, fmtSize(r.trash.bytes)), h("div", { class: "hint" }, `${r.trash.items} deleted item${r.trash.items === 1 ? "" : "s"} (.trash)`)));
  }
  function typeChart(byType) {
    return byType.length ? bars(byType, { label: "Space by type of file", value: (t) => t.bytes, name: (t) => t.type, text: (t) => `${fmtSize(t.bytes)} · ${t.files}` })
      : h("div", { class: "hint" }, "No files yet.");
  }

  D.addSpace({ id: "storage", icon: "💾", label: "Storage", order: 75, hash: "#/storage" });
  D.route("storage", async (page, args, current) => {
    mount(page, pageHead("Storage"), spinner());
    const place = args[0] || "mine";
    const years = Number(args[1]) || 2;
    const r = await api(`api/reports/storage?place=${encodeURIComponent(place)}&years=${years}`);
    if (!current()) return;
    const pick = h("select", { id: "storagePlace", "aria-label": "Folder" }, r.places.map((p) => h("option", { value: p.ref }, p.label)));
    pick.value = place;
    pick.addEventListener("change", () => go(`#/storage/${encodeURIComponent(pick.value)}`));
    const ctx = { showLocation: true };            // the location already names the space
    const yearSel = h("select", { id: "oldYears", "aria-label": "Not changed in" }, [1, 2, 3, 5, 10].map((y) => h("option", { value: String(y) }, `${y} year${y === 1 ? "" : "s"}`)));
    yearSel.value = String(years);
    yearSel.addEventListener("change", () => go(`#/storage/${encodeURIComponent(place)}/${yearSel.value}`));
    const dups = r.duplicateGroups.map((g) => h("div", { class: "dup-group card-inner", dataset: { sha: g.sha256.slice(0, 12) } },
      h("div", { class: "dup-head" }, h("strong", null, `${g.copies.length} copies of ${fmtSize(g.size)}`), h("span", { class: "hint" }, ` · ${fmtSize(g.extraBytes)} could be freed`)),
      h("div", { class: "item-list" }, g.copies.map((c) => D.itemRow(c, Object.assign({}, ctx, { rowExtra: () => h("button", { class: "btn-secondary btn-small keep-one", type: "button",
        onclick: async () => {
          if (!(await confirmDialog("Keep one", `Keep “${c.name}” (${c.location}) and move the other copies you may delete to Trash?`, "Keep this one"))) return;
          try { const out = await api("api/reports/duplicates/keep", { method: "POST", body: { keepId: c.id } }); toast(`${out.removed} moved to Trash${out.skipped ? `, ${out.skipped} you can't delete left` : ""}`); D.render(); } catch (e) { fail(e); }
        } }, "Keep this one") }))))));
    mount(page, pageHead("Storage", pick),
      tiles(r),
      h("div", { class: "card" }, h("h3", null, "By type"), typeChart(r.byType)),
      h("div", { class: "card" }, h("h3", null, "Growth over 12 months"), growthChart(r.growth)),
      h("div", { class: "card", id: "dupCard" }, h("h3", null, "Duplicates"), h("p", { class: "hint" }, "Files with exactly the same content (every copy you can open, wherever it is). Keep one moves the others to Trash."),
        dups.length ? dups : h("div", { class: "hint" }, "No duplicates.")),
      h("div", { class: "card" }, h("h3", null, "Largest files"), D.itemList(r.largest, ctx, "No files yet.")),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("h3", null, `Not changed in ${years} year${years === 1 ? "" : "s"}`), yearSel),
        h("p", { class: "hint" }, `${r.old.files} file${r.old.files === 1 ? "" : "s"}, ${fmtSize(r.old.bytes)}.`), D.itemList(r.old.items, ctx, "None.")),
      h("div", { class: "card" }, h("h3", null, "Empty folders"), D.itemList(r.emptyFolders, ctx, "None.")),
      h("p", { class: "hint" }, "History and Trash: earlier copies of documents and deleted items, kept by the app (Trash is emptied after an admin-set number of days). ", h("a", { href: "#/trash" }, "Open Trash")));
  });

  // ---------- Admin → Storage ----------
  if (window.Admin) Admin.addTab({ id: "storage", label: "Storage", order: 35, render: async (box, _a, current) => {
    const r = await api("api/admin/storage");
    if (!current()) return;
    const total = r.totals;
    const empty = h("button", { class: "btn-danger", type: "button", id: "emptyAllTrash", disabled: !r.trashItems, onclick: async () => {
      if (!(await confirmDialog("Empty all Trash now", `Delete ${r.trashItems} item${r.trashItems === 1 ? "" : "s"} in everyone's Trash and the shared folders' Trash for good? This can't be undone.`, "Empty all Trash", true))) return;
      try { const out = await api("api/admin/storage/empty-trash", { method: "POST" }); toast(`${out.removed} item${out.removed === 1 ? "" : "s"} deleted for good`); D.render(); } catch (e) { fail(e); }
    } }, "Empty all Trash now");
    const rows = r.folders.map((f) => h("tr", { dataset: { root: f.rootId } },
      h("td", null, f.kind === "person" ? `👤 ${f.personName || f.label}` : `📁 ${f.label}`, f.missing ? h("span", { class: "chip warn" }, "missing") : null),
      h("td", { class: "num" }, String(f.files)), h("td", { class: "num" }, fmtSize(f.bytes)),
      h("td", { class: "num" }, f.quota ? `${Math.round(f.bytes / f.quota.bytes * 100)}% of ${f.quota.gb} GB` : "—"),
      h("td", { class: "num" }, fmtSize(f.versions.bytes)), h("td", { class: "num" }, fmtSize(f.trash.bytes)),
      h("td", { class: "num" }, f.duplicates.groups ? `${f.duplicates.groups} · ${fmtSize(f.duplicates.extraBytes)}` : "—")));
    const typeAll = {};
    r.folders.forEach((f) => f.byType.forEach((t) => { const a = typeAll[t.type] || (typeAll[t.type] = { type: t.type, files: 0, bytes: 0 }); a.files += t.files; a.bytes += t.bytes; }));
    const growthAll = (r.folders[0] ? r.folders[0].growth : []).map((g, i) => ({ month: g.month, estimated: r.folders.some((f) => f.growth[i].estimated),
      bytes: r.folders.reduce((s, f) => s + (f.growth[i].bytes || 0), 0), files: r.folders.reduce((s, f) => s + (f.growth[i].files || 0), 0) }));
    mount(box,
      h("div", { class: "usage-tiles" },
        h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "Documents and files"), h("div", { class: "usage-big" }, fmtSize(total.bytes)), h("div", { class: "hint" }, `${total.files} files`)),
        h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "History (.versions)"), h("div", { class: "usage-big" }, fmtSize(total.versions))),
        h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "Trash (.trash)"), h("div", { class: "usage-big" }, fmtSize(total.trash))),
        h("div", { class: "usage-tile" }, h("div", { class: "usage-label" }, "The app's /data"), h("div", { class: "usage-big", id: "dataUse" }, fmtSize(r.data.total)),
          h("div", { class: "hint" }, `database ${fmtSize(r.data.database)}${r.data.free !== null ? ` · ${fmtSize(r.data.free)} free` : ""}`))),
      h("div", { class: "card" }, h("h3", null, "Each folder"), h("p", { class: "hint" }, "Sizes and counts only: the lists of files (largest, old, duplicates) are on each person's own Storage page, for what they can open."),
        h("div", { class: "table-scroll" }, h("table", { class: "usage-table", id: "storageTable" },
          h("thead", null, h("tr", null, ["Folder", "Files", "Size", "Limit", "History", "Trash", "Duplicates"].map((x, i) => h("th", { class: i ? "num" : "" }, x)))),
          h("tbody", null, rows)))),
      h("div", { class: "card" }, h("h3", null, "By type"), typeChart(Object.values(typeAll).sort((a, b) => b.bytes - a.bytes))),
      h("div", { class: "card" }, h("h3", null, "Growth over 12 months"), growthChart(growthAll)),
      h("div", { class: "card" }, h("h3", null, "Trash"), h("p", { class: "hint" }, `${r.trashItems} deleted item${r.trashItems === 1 ? "" : "s"} across everyone's Trash and the shared folders' Trash.`), h("div", { class: "actions" }, empty)));
  } });

  Object.assign(D, { bars, columns, sensorDialog, toggleFollow });
})();
