"use strict";
/* Household Docs — the Search page (SPEC §10): #/search/<"?q=…&type=pdf…" or just words>.
   The box takes words, "phrases", -words, OR, wildcards (*.pdf) and filters (type:pdf modified:7d size:>5mb …);
   the panel below sets where to look, how names match, and the filters. Every filter in use shows as a chip —
   tapping one removes it. Results: name (match highlighted), location, modified, by, size, type; sort, group,
   pages of 50, open at the match, show in folder, tick boxes for the selection bar, the list as CSV, save the
   search (pinned ones go in the sidebar), recent searches. Words search as you type; patterns run on Enter.
   Everything is filtered on the server, inside the query that checks access. */
(function () {
  const D = window.Docs;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, pageHead, fmtSize, fmtWhen, fmtFull, go } = D;
  const { mount, debounce } = UI;
  const T = window.DocsText;

  const DATES = [["", "Any time"], ["today", "Today"], ["yesterday", "Yesterday"], ["7d", "Last 7 days"], ["30d", "Last 30 days"],
    ["thismonth", "This month"], ["lastmonth", "Last month"], ["thisyear", "This year"], ["lastyear", "Last year"], ["custom", "Custom…"]];
  const SIZES = [["", "Any size"], ["tiny", "Under 100 KB"], ["small", "100 KB – 1 MB"], ["medium", "1–10 MB"], ["large", "10–100 MB"],
    ["huge", "Over 100 MB"], ["custom", "Custom…"]];
  const TYPES = [["note", "Note"], ["markdown", "Markdown"], ["checklist", "Checklist"], ["sheet", "Sheet"], ["folder", "Folder"],
    ["image", "Image"], ["pdf", "PDF"], ["spreadsheet", "Spreadsheet"], ["document", "Document"], ["archive", "Archive"],
    ["video", "Video"], ["audio", "Audio"], ["other", "Other"]];
  const IS = [["fav", "⭐ Favourites"], ["new", "● Changed since you looked"], ["open", "Checklists with open items"], ["done", "Checklists all done"], ["empty", "Empty folders"], ["dup", "Duplicates"]];
  const SORTS = [["relevance", "Relevance"], ["name", "Name"], ["modified", "Modified"], ["created", "Created"], ["size", "Size"], ["type", "Type"], ["location", "Location"]];
  const GROUPS = [["none", "No groups"], ["location", "By location"], ["type", "By type"], ["modified", "By date"]];
  const FILTER_KEYS = ["match", "nameMode", "contentMode", "scope", "sub", "trash", "modified", "created", "type", "ext", "size", "by", "owner",
    "shared", "access", "is", "path", "tag", "color"];
  const looksPattern = (st) => st.nameMode === "regex" || st.contentMode === "regex" || /(^|\s)-?(name|content):\//.test(st.q || "");

  function filtersOf(st) {
    const f = {};
    FILTER_KEYS.forEach((k) => {
      const v = st[k];
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) return;
      if ((k === "match" && v === "any") || (k === "nameMode" && v === "contains") || (k === "contentMode" && v === "words")) return;
      if (k === "sub" && v === true) return;
      f[k] = v;
    });
    return f;
  }
  function select(id, label, options, value, onChange) {
    const sel = h("select", { id, "aria-label": label, value: value || "" }, options.map(([v, l]) => h("option", { value: v }, l)));
    sel.addEventListener("change", () => onChange(sel.value, sel));
    return sel;
  }
  function field(label, ...control) { return h("label", { class: "sf-field" }, h("span", { class: "sf-label" }, label), control); }

  // ---------- the help popover ----------
  function helpDialog() {
    const row = (code, text) => h("div", { class: "help-row" }, h("code", null, code), h("span", null, text));
    openModal("Search tips", h("div", { class: "search-help" },
      h("h4", null, "Words"), row("budget car", "both, in the name or the text (each matches the start of a word)"),
      row('"old town"', "those words together"), row("-draft", "leave out what has it"), row("tent OR stove", "either"),
      h("h4", null, "Names"), row("*.pdf", "wildcards: * any text, ? one character, [0-9] one of these"),
      row("bill-2026-??.pdf", "e.g. bill-2026-01.pdf"), row("name:/^invoice-\\d{4}/", "a regular expression (Python)"),
      h("h4", null, "Text"), row("content:/\\b\\d{4}-\\d{4}\\b/", "a regular expression in the text (stopped after 5 seconds)"),
      h("h4", null, "Filters"), row("type:pdf  ext:xlsx", "type (note, checklist, sheet, folder, image, pdf, spreadsheet, document, archive, video, audio, other) or extension"),
      row("modified:7d  modified:>2026-09-01", "also today, yesterday, thismonth, lastyear, 2026-01-01..2026-03-31, <2025"),
      row("size:>5mb  size:100kb..1mb", "sizes in b, kb, mb, gb"), row("by:alex  by:me  by:outside", "who changed it last"),
      row("owner:priya", "whose it is"), row('in:"House papers"  in:mine  in:trash', "where to look"),
      row("path:taxes/2026", "inside these folders (wildcards work)"), row("is:fav  is:new  is:open  is:done  is:empty  is:dup  is:shared", "favourites, changed since you looked, checklists, empty folders, duplicates, shared"),
      row("shared:byme  shared:withme  shared:not", "sharing"), row("access:edit  access:read", "what you can change"),
      row('tag:taxes  -tag:car  tag:"house papers"', "tags (or not)"), row("color:red", "a colour: red, orange, yellow, green, teal, blue, purple, grey")), { wide: true });
  }

  // ---------- the page ----------
  let lastRun = 0;
  D.route("search", async (page, args, current) => {
    const me = D.state.me;
    const st = Object.assign({ q: "", match: "any", nameMode: "contains", contentMode: "words", sort: "relevance", group: "none", page: 1, sub: true },
      T.parseSearchArg(args.join("/")));
    const header = document.getElementById("searchInput");
    if (header && document.activeElement !== header) header.value = st.q || "";
    const box = h("input", { type: "search", id: "pageSearch", value: st.q || "", maxlength: "400", autocomplete: "off",
      placeholder: "Words, *.pdf, type:pdf modified:7d …", "aria-label": "Search" });
    const errBox = h("div", { class: "error-text", role: "alert", id: "searchError" });
    const chipsBox = h("div", { class: "chips-row", id: "searchChips" });
    const resultsBox = h("div", { class: "results-box", id: "searchResults" });
    const sideBox = h("div", { class: "search-side", id: "searchSide" });
    const form = h("form", { class: "search-page-form", role: "search", onsubmit: (e) => { e.preventDefault(); commit(true); } },
      box, h("button", { class: "btn-primary", type: "submit", id: "searchGo" }, "Search"),
      h("button", { class: "icon-btn", type: "button", title: "Search tips", "aria-label": "Search tips", id: "searchHelp", onclick: helpDialog }, "?"));
    const panel = h("details", { class: "card search-panel", id: "searchPanel", open: matchMedia("(min-width: 761px)").matches },
      h("summary", null, "Where to look, how to match, filters"), h("div", { class: "sf-body" }));

    function setOpt(k, v, now = true) {
      if (v === "" || v === null || v === undefined || (Array.isArray(v) && !v.length)) delete st[k]; else st[k] = v;
      st.page = 1;
      if (now) commit(false);
    }
    function commit(push) {
      st.q = box.value.trim();
      if (header && document.activeElement !== header) header.value = st.q;
      const arg = T.searchArg(Object.assign({}, st, { page: st.page > 1 ? st.page : undefined, sub: st.sub === false ? "0" : undefined,
        match: st.match === "any" ? undefined : st.match, nameMode: st.nameMode === "contains" ? undefined : st.nameMode,
        contentMode: st.contentMode === "words" ? undefined : st.contentMode, sort: st.sort === "relevance" ? undefined : st.sort,
        group: st.group === "none" ? undefined : st.group }));
      const hash = "#/search/" + encodeURIComponent(arg);
      if (push) {
        if (st.q || Object.keys(filtersOf(st)).length) api("api/searches/recent", { method: "POST", body: { q: st.q, filters: filtersOf(st) } }).catch(() => {});
        if (location.hash !== hash) { go(hash); return; }
      } else history.replaceState(history.state, "", hash);
      run();
    }

    // ---- the panel: where, how, filters ----
    function drawPanel() {
      const body = panel.querySelector(".sf-body");
      const scopes = st.scope || [];
      const scopeBtn = (v, label) => h("button", { type: "button", class: "chip-toggle" + ((v === "all" ? !scopes.length : scopes.includes(v)) ? " on" : ""),
        dataset: { scope: v }, "aria-pressed": String(v === "all" ? !scopes.length : scopes.includes(v)),
        onclick: () => {
          if (v === "all") setOpt("scope", []);
          else setOpt("scope", scopes.includes(v) ? scopes.filter((x) => x !== v) : scopes.filter((x) => !x.startsWith("folder:")).concat([v]));
          drawPanel();
        } }, label);
      const here = scopes.find((x) => x.startsWith("folder:") || (x.startsWith("root:") && !(me.sharedFolders || []).some((f) => "root:" + f.rootId === x)));
      const folderScopes = (me.sharedFolders || []).filter((f) => f.exists).map((f) => scopeBtn("root:" + f.rootId, "🗂️ " + f.label));
      const dateSel = (key, label) => {
        const v = st[key] || "";
        const preset = DATES.some(([x]) => x === v) ? v : "custom";
        const from = h("input", { type: "date", "aria-label": label + " from", value: v.includes("..") ? v.split("..")[0] : "" });
        const to = h("input", { type: "date", "aria-label": label + " to", value: v.includes("..") ? v.split("..")[1] : "" });
        const custom = h("span", { class: "sf-custom", hidden: preset !== "custom" }, from, "–", to);
        const apply = () => { if (from.value || to.value) setOpt(key, `${from.value}..${to.value}`); };
        from.addEventListener("change", apply);
        to.addEventListener("change", apply);
        const sel = select("sf-" + key, label, DATES, preset, (val) => { custom.hidden = val !== "custom"; if (val !== "custom") setOpt(key, val); });
        return field(label, sel, custom);
      };
      const sizeVal = st.size || "";
      const sizePreset = SIZES.some(([x]) => x === sizeVal) ? sizeVal : "custom";
      const sizeCustom = h("input", { type: "text", "aria-label": "Size (e.g. >5mb)", placeholder: ">5mb or 1mb..10mb", value: sizePreset === "custom" ? sizeVal : "",
        hidden: sizePreset !== "custom", class: "sf-small" });
      sizeCustom.addEventListener("change", () => setOpt("size", sizeCustom.value.trim()));
      const people = [["", "Anyone"], ["me", "Me"], ["notme", "Not me"], ["outside", "Changed outside the app"]].concat((D.state.people || []).filter((p) => !p.you).map((p) => [p.id, p.name]));
      const owners = [["", "Anyone"], ["me", "Me"]].concat((D.state.people || []).filter((p) => !p.you).map((p) => [p.id, p.name]));
      const types = st.type || [];
      const typeBox = h("div", { class: "chip-set", id: "sfTypes" }, TYPES.map(([v, l]) => h("button", { type: "button", class: "chip-toggle" + (types.includes(v) ? " on" : ""),
        dataset: { type: v }, "aria-pressed": String(types.includes(v)), onclick: () => { setOpt("type", types.includes(v) ? types.filter((x) => x !== v) : types.concat([v])); drawPanel(); } }, l)));
      const iss = st.is || [];
      const isBox = h("div", { class: "chip-set", id: "sfIs" }, IS.map(([v, l]) => h("button", { type: "button", class: "chip-toggle" + (iss.includes(v) ? " on" : ""),
        dataset: { is: v }, "aria-pressed": String(iss.includes(v)), onclick: () => { setOpt("is", iss.includes(v) ? iss.filter((x) => x !== v) : iss.concat([v])); drawPanel(); } }, l)));
      const ext = h("input", { type: "text", class: "sf-small", id: "sfExt", "aria-label": "Extension", placeholder: "pdf, xlsx", value: (st.ext || []).join(", ") });
      ext.addEventListener("change", () => setOpt("ext", ext.value.split(/[,\s]+/).map((x) => x.replace(/^\./, "")).filter(Boolean)));
      const path = h("input", { type: "text", class: "sf-small", id: "sfPath", "aria-label": "Path", placeholder: "taxes/2026", value: st.path || "" });
      path.addEventListener("change", () => setOpt("path", path.value.trim()));
      // tags and colours (§17.1)
      const knownTags = ((me.tags) || []).map((t) => t.tag);
      const tagIn = h("input", { type: "text", class: "sf-small", id: "sfTag", "aria-label": "Tag", placeholder: "taxes, car", list: "sfTagList",
        value: (st.tag || []).join(", ") });
      tagIn.addEventListener("change", () => setOpt("tag", tagIn.value.split(",").map((x) => x.replace(/^#/, "").trim()).filter(Boolean)));
      const tagList = h("datalist", { id: "sfTagList" }, knownTags.map((t) => h("option", { value: t })));
      const colours = [["", "Any colour"]].concat((D.COLOURS || []).map((c) => [c, c[0].toUpperCase() + c.slice(1)]));
      const nameModes = [["contains", "Contains"], ["starts", "Starts with"], ["exact", "Exact"], ["ends", "Ends with"], ["wildcard", "Wildcard (* ?)"]]
        .concat(me.app.regexSearch ? [["regex", "Regular expression"]] : []);
      mount(body,
        h("div", { class: "sf-row" }, h("span", { class: "sf-label" }, "Look in"), h("div", { class: "chip-set", id: "sfScopes" },
          scopeBtn("all", "Everywhere"), here ? scopeBtn(here, "📁 This folder") : null, scopeBtn("mine", "My docs"), scopeBtn("shared", "Shared with me"),
          scopeBtn("everyone", "Everyone"), (me.sharedFolders || []).length ? scopeBtn("folders", "All shared folders") : null, folderScopes),
          here && here.startsWith("folder:") ? h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", checked: st.sub !== false, onchange: (e) => setOpt("sub", e.target.checked ? true : false) }), "With subfolders") : null,
          h("label", { class: "mini-toggle" }, h("input", { type: "checkbox", id: "sfTrash", checked: !!st.trash, onchange: (e) => setOpt("trash", e.target.checked) }), "Include Trash")),
        h("div", { class: "sf-grid" },
          field("Match", select("sfMatch", "Match", [["any", "Name or text"], ["name", "Name only"], ["content", "Text only"]], st.match, (v) => setOpt("match", v))),
          field("Names", select("sfNameMode", "How names match", nameModes, st.nameMode, (v) => setOpt("nameMode", v))),
          field("Text", select("sfContentMode", "How text matches", [["words", "Words"]].concat(me.app.regexSearch ? [["regex", "Regular expression"]] : []), st.contentMode, (v) => setOpt("contentMode", v))),
          dateSel("modified", "Modified"), dateSel("created", "Created"),
          field("Size", select("sfSize", "Size", SIZES, sizePreset, (v) => { sizeCustom.hidden = v !== "custom"; if (v !== "custom") setOpt("size", v); }), sizeCustom),
          field("Modified by", select("sfBy", "Modified by", people, st.by, (v) => setOpt("by", v))),
          field("Owner", select("sfOwner", "Owner", owners, st.owner, (v) => setOpt("owner", v))),
          field("Shared", select("sfShared", "Shared", [["", "Any"], ["byme", "Shared by me"], ["withme", "Shared with me"], ["not", "Not shared"], ["everyone", "Shared with Everyone"]], st.shared, (v) => setOpt("shared", v))),
          field("Access", select("sfAccess", "Access", [["", "Any"], ["edit", "I can edit"], ["read", "Read only"]], st.access, (v) => setOpt("access", v))),
          field("Extension", ext), field("Path", path), field("Tag", tagIn, tagList),
          field("Colour", select("sfColor", "Colour", colours, st.color, (v) => setOpt("color", v)))),
        h("div", { class: "sf-row" }, h("span", { class: "sf-label" }, "Type"), typeBox),
        h("div", { class: "sf-row" }, h("span", { class: "sf-label" }, "Other"), isBox));
    }

    // ---- results ----
    let seq = 0;
    async function run() {
      const my = ++seq;
      lastRun = my;
      errBox.textContent = "";
      const params = T.searchArg(Object.assign({}, st, { sub: st.sub === false ? "0" : undefined }));
      if (!st.q && !Object.keys(filtersOf(st)).length) { mount(chipsBox); mount(resultsBox); drawSide(); return; }
      mount(resultsBox, spinner());
      let d;
      try { d = await api("api/search" + params); }
      catch (e) { if (my === seq) { errBox.textContent = e.message; mount(resultsBox); } return; }
      if (my !== seq || !current()) return;
      mount(sideBox);
      drawChips(d);
      drawResults(d);
    }
    function drawChips(d) {
      mount(chipsBox, d.chips.map((c) => h("button", { type: "button", class: "chip filter-chip" + (c.error ? " warn" : ""), title: "Remove",
        dataset: { chip: c.key }, onclick: () => {
          if (c.token) { box.value = T.removeToken(box.value, c.token); }
          else if (c.param === "scope" && c.value) setOpt("scope", (st.scope || []).filter((x) => x !== c.value), false);
          else if (c.param === "trash") setOpt("trash", false, false);
          else if (c.param && c.value && Array.isArray(st[c.param])) setOpt(c.param, st[c.param].filter((x) => x !== c.value), false);
          else if (c.param) setOpt(c.param, "", false);
          drawPanel();
          commit(true);
        } }, c.label, h("span", { class: "chip-x", "aria-hidden": "true" }, " ✕"))));
    }
    function openResult(it) {
      api("api/searches/recent", { method: "POST", body: { q: st.q, filters: filtersOf(st) } }).catch(() => {});
      if (it.inTrash) { go("#/trash"); return; }
      if (it.cell) go(`#/doc/${encodeURIComponent(it.id)}/cell/${encodeURIComponent(it.cell.tab)}/${encodeURIComponent(it.cell.ref)}`);
      else if (it.document && it.line) go(`#/doc/${encodeURIComponent(it.id)}/at/${it.line}`);
      else D.openItem(it);
    }
    function drawResults(d) {
      const items = d.results;
      const sel = D.makeSelection(items, () => draw());
      const sortSel = select("sfSort", "Sort by", SORTS, st.sort, (v) => { st.sort = v; delete st.dir; st.page = 1; commit(false); });
      const dirBtn = st.sort === "relevance" ? null : h("button", { class: "icon-btn", type: "button", id: "sfDir", title: "Ascending or descending", "aria-label": "Ascending or descending",
        onclick: () => { st.dir = (d.options.dir === "asc") ? "desc" : "asc"; commit(false); } }, d.options.dir === "asc" ? "↑" : "↓");
      const groupSel = select("sfGroup", "Group", GROUPS, st.group, (v) => { st.group = v; commit(false); });
      const count = d.searched ? `${d.total}${d.more ? "+" : ""} result${d.total === 1 ? "" : "s"} · ${d.ms} ms` : "";
      const exportA = h("a", { class: "btn-ghost btn-small", id: "exportCsv", href: "api/search/export" + T.searchArg(Object.assign({}, st, { page: undefined, sub: st.sub === false ? "0" : undefined })), download: "" }, "⬇ CSV");
      const save = h("button", { class: "btn-ghost btn-small", type: "button", id: "saveSearch", onclick: () => saveDialog() }, "☆ Save search");
      const tools = h("div", { class: "results-tools" }, h("span", { class: "hint", id: "resultCount" }, count),
        h("span", { class: "results-ctl" }, sortSel, dirBtn, groupSel, save, exportA));
      const notices = (d.notices || []).map((n) => h("div", { class: "notice" }, n));
      if (d.stopped) notices.unshift(h("div", { class: "notice warn", id: "stoppedNotice" }, "Search stopped after 5 s — narrow it down. These are the matches found so far."));
      const bar = h("div", { class: "select-bar-slot" });
      const list = h("div", { class: "result-list", role: "list" });
      const pager = d.pages > 1 ? h("div", { class: "pager" },
        h("button", { class: "btn-ghost btn-small", type: "button", disabled: d.page <= 1, onclick: () => { st.page = d.page - 1; commit(false); window.scrollTo(0, 0); } }, "‹ Previous"),
        h("span", { class: "hint" }, `Page ${d.page} of ${d.pages}`),
        h("button", { class: "btn-ghost btn-small", type: "button", id: "nextPage", disabled: d.page >= d.pages, onclick: () => { st.page = d.page + 1; commit(false); window.scrollTo(0, 0); } }, "Next ›")) : null;
      function row(it) {
        const tick = h("label", { class: "row-tick" }, h("input", { type: "checkbox", checked: sel.ids.has(it.id), "aria-label": `Select ${it.name}`, onchange: (e) => sel.toggle(it, e.target.checked) }));
        const name = h("button", { class: "res-name", type: "button", onclick: () => openResult(it), title: it.name },
          D.iconBox(it, "row-icon"),
          h("span", { class: "row-text" }, h("span", { class: "row-name" }, h("span", { class: "nm" }, it.nameMarked ? D.highlight(it.nameMarked) : it.name),
            it.changed ? h("span", { class: "new-dot", title: "Changed since you last looked", "aria-label": "Changed since you last looked" }) : null,
            it.favourite ? h("span", { class: "row-badge" }, "⭐") : null, it.inTrash ? h("span", { class: "chip warn" }, "in Trash") : null,
            (it.tags || []).slice(0, 3).map((t) => h("span", { class: "tag-chip" }, "#" + t))),
            it.snippet ? h("span", { class: "row-snippet" }, D.highlight(it.snippet)) : null));
        const loc = h("span", { class: "res-loc", title: it.location }, it.location);
        const more = h("button", { class: "icon-btn row-more", type: "button", "aria-label": `More for ${it.name}`, onclick: (e) => D.itemMenu(e.currentTarget, it, { inSearch: true, showLocation: true }) }, "⋯");
        return h("div", { class: "res-row" + (sel.ids.has(it.id) ? " selected" : ""), role: "listitem", dataset: { id: it.id, kind: it.kind } }, tick, name, loc,
          h("span", { class: "res-mod", title: fmtFull(it.modified) }, fmtWhen(it.modified)),
          h("span", { class: "res-by" }, it.updatedByName || (it.updatedBy ? "" : "outside the app")),
          h("span", { class: "res-size" }, it.kind === "folder" ? "" : fmtSize(it.size)), h("span", { class: "res-type" }, it.typeLabel), more);
      }
      function draw() {
        const kids = [];
        let g = null;
        items.forEach((it) => {
          if (st.group !== "none" && it.group !== g) { g = it.group; kids.push(h("div", { class: "res-group", role: "presentation" }, g)); }
          kids.push(row(it));
        });
        const head = items.length ? h("div", { class: "res-row res-head", "aria-hidden": "true" },
          h("label", { class: "row-tick" }, h("input", { type: "checkbox", "aria-label": "Select all on this page", checked: sel.count() === items.length,
            onchange: (e) => (e.target.checked ? sel.all() : sel.clear()) })),
          h("span", null, "Name"), h("span", null, "Location"), h("span", null, "Modified"), h("span", null, "By"), h("span", null, "Size"), h("span", null, "Type"), h("span")) : null;
        mount(list, items.length ? [head, kids] : h("div", { class: "empty" }, d.searched ? "Nothing found. Try fewer words, another place, or a pattern like *.pdf." : ""));
        mount(bar, D.selectionBar(sel, { inSearch: true }));
      }
      draw();
      mount(resultsBox, tools, notices, bar, h("div", { class: "card list-card" }, list), pager);
    }
    function saveDialog() {
      const name = h("input", { type: "text", id: "saveName", maxlength: "80", value: st.q || "My search", "aria-label": "Name" });
      const pin = h("input", { type: "checkbox", id: "savePin", checked: true });
      const err = h("div", { class: "error-text", role: "alert" });
      const m = openModal("Save this search", h("form", { onsubmit: async (e) => {
        e.preventDefault();
        try {
          await api("api/searches", { method: "POST", body: { name: name.value.trim(), q: st.q, filters: filtersOf(st), pinned: pin.checked } });
          m.close();
          toast(pin.checked ? "Saved — it's in the sidebar" : "Saved");
          await D.refreshMe();
          D.drawNav();
        } catch (x) { err.textContent = x.message; }
      } }, h("label", { class: "field" }, "Name", name), h("label", { class: "mini-toggle" }, pin, "Pin to the sidebar"), err,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"), h("button", { class: "btn-primary", type: "submit", id: "saveGo" }, "Save"))));
      setTimeout(() => { name.focus(); name.select(); }, 40);
    }
    async function drawSide() {
      let rec = [], saved = [];
      try { [rec, saved] = await Promise.all([api("api/searches/recent").then((r) => r.recent), api("api/searches").then((r) => r.searches)]); } catch (e) { return; }
      if (!current() || st.q || Object.keys(filtersOf(st)).length) return;
      const link = (s, label) => h("a", { class: "item-row side-search", href: D.searchHash(Object.assign({ q: s.q }, s.filters)) },
        h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, "🔎"), h("span", { class: "row-text" },
          h("span", { class: "row-name" }, label), h("span", { class: "row-meta" }, [s.q, ...Object.keys(s.filters || {}).map((k) => `${k}: ${[].concat(s.filters[k]).join(", ")}`)].filter(Boolean).join(" · ")))));
      mount(sideBox,
        saved.length ? h("div", { class: "card list-card" }, h("div", { class: "card-head list-card-title" }, h("h3", null, "Saved searches"), h("a", { class: "link-btn", href: "#/searches" }, "Manage")),
          h("div", { class: "item-list" }, saved.map((s) => link(s, (s.pinned ? "📌 " : "") + s.name)))) : null,
        rec.length ? h("div", { class: "card list-card", id: "recentSearches" }, h("div", { class: "card-head list-card-title" }, h("h3", null, "Recent searches"),
          h("button", { class: "link-btn", type: "button", onclick: async () => { await api("api/searches/recent", { method: "DELETE" }); drawSide(); } }, "Clear")),
          h("div", { class: "item-list" }, rec.map((s) => link(s, s.q || "(filters only)")))) : null,
        !saved.length && !rec.length ? h("p", { class: "hint" }, "Search names and the text inside notes, checklists and other text files — everything you can open, including the shared folders you have. Tap ? for patterns and filters.") : null);
    }

    box.addEventListener("input", debounce(() => {
      if (!current()) return;
      if (looksPattern(Object.assign({}, st, { q: box.value }))) return;           // patterns run on Enter
      if (box.value.trim().length >= 2 || !box.value.trim()) commit(false);
    }, 250));
    if (!D.state.people) { try { await D.people(); } catch (e) { /* the pickers just lack names */ } }
    if (!current()) return;
    mount(page, pageHead("Search"), form, errBox, panel, chipsBox, resultsBox, sideBox);
    drawPanel();
    run();
    if (!st.q && matchMedia("(pointer: fine)").matches) setTimeout(() => box.focus(), 30);
  });

  // the header's box: words open the Search page (Enter there runs patterns too)
  D.searchFromHeader = function (q, enter) {
    if (D.state.route === "search") {
      const box = document.getElementById("pageSearch");
      if (box) { box.value = q; box.dispatchEvent(new Event(enter ? "change" : "input")); if (enter) box.form.requestSubmit(); return; }
    }
    if (!enter && /(^|\s)-?(name|content):\//.test(q)) return;
    go(D.searchHash({ q }));
  };
  D.addAction({ id: "searchin", label: "Search in this folder", icon: "🔎", order: 82, show: (it) => it.kind === "folder" && !it.inTrash,
    run: (it) => go(D.searchHash({ scope: ["folder:" + it.id] })) });

  // ---------- #/searches: saved searches (pin, rename, delete) and recent ones ----------
  D.route("searches", async (page, _a, current) => {
    mount(page, pageHead("Saved searches"), spinner());
    const [s, r] = await Promise.all([api("api/searches"), api("api/searches/recent")]);
    if (!current()) return;
    const again = async () => { await D.refreshMe(); D.render(); };
    const rows = s.searches.map((x) => h("div", { class: "item-row", dataset: { saved: x.id } },
      h("a", { class: "row-main", href: D.searchHash(Object.assign({ q: x.q }, x.filters)) }, h("span", { class: "row-icon", "aria-hidden": "true" }, x.pinned ? "📌" : "🔎"),
        h("span", { class: "row-text" }, h("span", { class: "row-name" }, x.name), h("span", { class: "row-meta" }, [x.q, ...Object.keys(x.filters).map((k) => `${k}: ${[].concat(x.filters[k]).join(", ")}`)].filter(Boolean).join(" · ")))),
      h("button", { class: "btn-ghost btn-small", type: "button", dataset: { pin: x.id }, onclick: async () => { try { await api(`api/searches/${x.id}`, { method: "PATCH", body: { pinned: !x.pinned } }); again(); } catch (e) { fail(e); } } }, x.pinned ? "Unpin" : "Pin"),
      h("button", { class: "btn-ghost btn-small", type: "button", onclick: async () => {
        const name = await D.askName("Rename search", "Name", x.name, "Rename");
        if (name) { try { await api(`api/searches/${x.id}`, { method: "PATCH", body: { name } }); again(); } catch (e) { fail(e); } }
      } }, "Rename"),
      h("button", { class: "icon-btn danger", type: "button", "aria-label": `Delete ${x.name}`, onclick: async () => {
        if (!(await confirmDialog("Delete search", `Delete the saved search “${x.name}”?`, "Delete", true))) return;
        try { await api(`api/searches/${x.id}`, { method: "DELETE" }); again(); } catch (e) { fail(e); }
      } }, "✕")));
    const recent = r.recent.map((x) => h("a", { class: "item-row", href: D.searchHash(Object.assign({ q: x.q }, x.filters)) },
      h("span", { class: "row-main static" }, h("span", { class: "row-icon", "aria-hidden": "true" }, "🕘"), h("span", { class: "row-text" },
        h("span", { class: "row-name" }, x.q || "(filters only)"), h("span", { class: "row-meta" }, fmtWhen(x.usedAt))))));
    mount(page, pageHead("Saved searches", h("a", { class: "btn-primary", href: "#/search" }, "🔎 New search")),
      h("div", { class: "card list-card" }, rows.length ? h("div", { class: "item-list" }, rows) : h("div", { class: "empty" }, "Nothing saved yet — use ☆ Save search on the Search page.")),
      h("h3", { class: "section-title" }, "Recent searches"),
      h("div", { class: "card list-card" }, recent.length ? h("div", { class: "item-list" }, recent) : h("div", { class: "empty" }, "No searches yet.")));
  });
})();
