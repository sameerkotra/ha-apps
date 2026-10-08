"use strict";
/* Household Docs — the sheet editor (SPEC §8, §17.10, §17.12). Registered on window.Docs: the "sheet" kind,
   ➕ New → Sheet and → Import a sheet, and Docs.editors.sheet. The formulas are SheetCalc's (sheetcalc.js).

   The grid draws only the rows on screen (5 000 rows × 100 columns fit); frozen rows and columns stay put with
   position: sticky. Changes are kept as cells with what they were when this page last heard from the server
   ("was"), and saved 1 s after the last change: the server applies a cell only if nobody changed it meanwhile
   (SPEC §7.2). Inserting or deleting rows / columns, sorting and tab changes send the whole sheet instead.
   Nothing a person typed is ever parsed as HTML (UI.h sets textContent; charts are SVG built with
   createElementNS). */
(function () {
  const D = window.Docs;
  const S = window.SheetCalc;
  const { h, api, toast, fail, openModal, confirmDialog, spinner, titleOf, go, fmtWhen, fmtFull } = D;
  const { $, mount, debounce, lsGet, lsSet } = UI;

  const RH = 28;                // row height (px), as in style.css
  const RHEAD = 46;             // row-number column width
  const DEFAULT_W = 110;
  const MAXR = 5000, MAXC = 100, MAXTABS = 10, MAXCELLS = 200000;
  const MIN_ROWS = 1000, MIN_COLS = 26;        // the empty grid shown (rows are drawn only as they scroll in)
  const FORMATS = [["general", "General"], ["number", "Number"], ["currency", "Currency"], ["accounting", "Accounting"], ["percent", "Percent"],
    ["scientific", "Scientific"], ["date", "Date"], ["time", "Time"], ["datetime", "Date and time"], ["text", "Text"]];
  const NO_DECIMALS = new Set(["date", "time", "datetime", "text"]);
  function hhmm(serial) {
    const secs = Math.round((serial - Math.floor(serial)) * 86400) % 86400;
    const two = (n) => String(n).padStart(2, "0");
    return `${two(Math.floor(secs / 3600))}:${two(Math.floor(secs / 60) % 60)}` + (secs % 60 ? ":" + two(secs % 60) : "");
  }
  const TOTALS = ["SUM", "AVERAGE", "COUNT", "MIN", "MAX"];
  const COLOURS = [["red", "Red"], ["amber", "Amber"], ["green", "Green"], ["blue", "Blue"], ["purple", "Purple"], ["grey", "Grey"]];
  const RULES = [["gt", "Greater than"], ["lt", "Less than"], ["between", "Between"], ["contains", "Text contains"], ["before", "Date before"], ["after", "Date after"], ["top", "Top N"], ["bottom", "Bottom N"], ["dup", "Duplicates"]];
  const SVGNS = "http://www.w3.org/2000/svg";
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const fine = () => matchMedia("(pointer: fine)").matches;

  // ---------------------------------------------------------------- kind, ➕ New, import
  D.kind("sheet", { icon: "📊", label: "Sheet", open: (it) => go("#/doc/" + it.id) });
  D.addNew({ id: "sheet", icon: "📊", label: "Sheet", order: 30, show: () => !D.isChild(), run: async (ctx) => {
    const fmt = (D.state.me && D.state.me.prefs && D.state.me.prefs.newSheets) || "xlsx";
    const name = await D.askName("New sheet", "Name", "");
    if (!name) return;
    try {
      const it = await api("api/docs", { method: "POST", body: { kind: "sheet", name, parentId: ctx.folderId, format: fmt } });
      toast(`Sheet made in ${ctx.label}`);
      D.openItem(it);
    } catch (e) { fail(e); }
  } });
  D.addNew({ id: "import-sheet", icon: "📥", label: "Import a sheet (.csv, .xlsx)", order: 86, show: () => !D.isChild(), run: (ctx) => {
    const input = h("input", { type: "file", accept: ".csv,.xlsx,text/csv", hidden: true });
    input.addEventListener("change", async () => {
      const f = input.files[0];
      input.remove();
      if (!f) return;
      const fmt = (D.state.me && D.state.me.prefs && D.state.me.prefs.newSheets) || "xlsx";
      try {
        const q = `name=${encodeURIComponent(f.name)}&format=${/\.xlsx$/i.test(f.name) ? "xlsx" : fmt}` + (ctx.folderId ? `&parentId=${encodeURIComponent(ctx.folderId)}` : "");
        const res = await fetch("api/import?" + q, { method: "POST", body: f, headers: { "Content-Type": "application/octet-stream" }, credentials: "same-origin" });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : (body.detail && body.detail.message) || "Import failed.");
        toast(body.leftOut && body.leftOut.length ? `Imported — left out: ${body.leftOut.join(", ")}` : `Imported into ${ctx.label}`, false, { ms: 6000 });
        D.openItem(body);
      } catch (e) { fail(e); }
    });
    document.body.appendChild(input);
    input.click();
  } });
  D.addAction({ id: "export-xlsx", label: "Download as .xlsx", icon: "📊", order: 71, show: (it) => it.kind === "sheet" && it.ext === "csv",
    run: (it) => D.saveUrl(`api/docs/${encodeURIComponent(it.id)}/export?format=xlsx`) });
  D.saveUrl = (url) => { const a = h("a", { href: url, download: "" }); document.body.appendChild(a); a.click(); a.remove(); };

  // ---------------------------------------------------------------- helpers
  function todayIn(tz) {
    try {
      const p = new Intl.DateTimeFormat("en-CA", { timeZone: tz || undefined, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
      const g = (t) => +p.find((x) => x.type === t).value;
      return S.dateSerial(g("year"), g("month"), g("day"));
    } catch (e) { return S.todaySerial(); }
  }
  const cellKey = (tab, ref) => tab + "\u0001" + ref;
  const contentOf = (cell) => {
    if (!cell) return JSON.stringify([null, false, null, null, false, false, null]);
    let v = cell.v === undefined ? null : cell.v;
    return JSON.stringify([v, !!cell.q, cell.f && cell.f !== "general" ? cell.f : null, cell.d ?? null, !!cell.red, !!cell.b, cell.al || null]);
  };
  const isFormulaCell = (cell) => cell && typeof cell.v === "string" && cell.v.length > 1 && cell.v[0] === "=" && !cell.q;
  function wire(v) {           // a computed value as the server stores it
    if (S.isErr(v)) return { e: v.code };
    if (typeof v === "number" && !isFinite(v)) return { e: "#NUM!" };
    return v === undefined ? null : v;
  }
  function fromWire(v) { return v && typeof v === "object" && v.e ? (S.ERR[v.e] || S.ERR["#VALUE!"]) : v; }
  let numStyle = "auto";        // the person's number style (Settings): "de" edits 1234,5 with a decimal comma
  function inputText(cell) {   // what the cell editor shows
    if (!cell || cell.v === null || cell.v === undefined) return "";
    if (cell.q) return "'" + cell.v;
    if (typeof cell.v === "boolean") return cell.v ? "TRUE" : "FALSE";
    if (typeof cell.v === "number") {
      const dec = (t) => (numStyle === "de" ? t.replace(".", ",") : t);
      if (cell.f === "date" || cell.f === "datetime") {
        const p = S.serialParts(cell.v), day = `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
        return cell.f === "date" ? day : `${day} ${hhmm(cell.v)}`;
      }
      if (cell.f === "time") return hhmm(cell.v);
      if (cell.f === "percent") return dec(S.numText(Math.round(cell.v * 100 * 1e10) / 1e10)) + "%";
      return dec(S.numText(cell.v));
    }
    return String(cell.v);
  }
  function rangeOf(sel) { return { c1: Math.min(sel.c1, sel.c2), r1: Math.min(sel.r1, sel.r2), c2: Math.max(sel.c1, sel.c2), r2: Math.max(sel.r1, sel.r2) }; }
  function rangeText(r) { const a = S.addr(r.c1, r.r1), b = S.addr(r.c2, r.r2); return a === b ? a : a + ":" + b; }
  function usedOf(tab) {
    let mr = -1, mc = -1;
    for (const ref of Object.keys(tab.cells)) { const p = S.parseRef(ref); if (p) { if (p.r > mr) mr = p.r; if (p.c > mc) mc = p.c; } }
    return { rows: mr + 1, cols: mc + 1 };
  }
  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
    for (const k of kids.flat()) if (k !== null && k !== undefined) el.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    return el;
  }

  // ---------------------------------------------------------------- charts (SVG in theme colours: style.css .chart-s0…)
  function chartData(tabModel, value, rng) {
    const r = S.parseRange(rng);
    if (!r) return null;
    const val = (c, row) => value(tabModel, c, row);
    const series = [];
    const cats = [];
    const multi = r.c2 > r.c1;
    const headerRow = (() => { for (let c = r.c1 + (multi ? 1 : 0); c <= r.c2; c++) { const v = val(c, r.r1); if (typeof v === "string") return true; } return false; })();
    const first = headerRow ? r.r1 + 1 : r.r1;
    for (let row = first; row <= r.r2; row++) cats.push(multi ? S.toText(val(r.c1, row)) : String(row + 1));
    for (let c = multi ? r.c1 + 1 : r.c1; c <= r.c2; c++) {
      const name = headerRow ? S.toText(val(c, r.r1)) : S.colName(c);
      const data = [];
      for (let row = first; row <= r.r2; row++) { const v = val(c, row); data.push(typeof v === "number" && isFinite(v) ? v : 0); }
      series.push({ name, data });
    }
    return { cats, series };
  }
  function drawChart(def, data, opts = {}) {
    const W = opts.w || 560, H = opts.h || 300;
    const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img", "aria-label": def.title || "Chart" });
    if (!data || !data.series.length || !data.cats.length) { root.appendChild(svg("text", { x: W / 2, y: H / 2, class: "chart-label", "text-anchor": "middle" }, "No numbers in this range")); return root; }
    const top = def.title ? 30 : 12;
    if (def.title) root.appendChild(svg("text", { x: W / 2, y: 20, class: "chart-title", "text-anchor": "middle" }, def.title));
    if (def.type === "pie") {
      const s = data.series[0];
      const total = s.data.reduce((a, b) => a + Math.max(0, b), 0);
      const cx = W * 0.35, cy = (H + top) / 2, rad = Math.min(W * 0.3, (H - top) / 2 - 8);
      let ang = -Math.PI / 2;
      s.data.forEach((v, i) => {
        const part = total ? Math.max(0, v) / total : 0;
        const a2 = ang + part * Math.PI * 2;
        const large = part > 0.5 ? 1 : 0;
        const p = part >= 0.9999 ? `M ${cx} ${cy - rad} A ${rad} ${rad} 0 1 1 ${cx - 0.01} ${cy - rad} Z`
          : `M ${cx} ${cy} L ${cx + rad * Math.cos(ang)} ${cy + rad * Math.sin(ang)} A ${rad} ${rad} 0 ${large} 1 ${cx + rad * Math.cos(a2)} ${cy + rad * Math.sin(a2)} Z`;
        if (part > 0) root.appendChild(svg("path", { d: p, class: `chart-s${i % 8} chart-slice` }, svg("title", null, `${data.cats[i]}: ${S.numText(v)}`)));
        ang = a2;
      });
      data.cats.slice(0, 12).forEach((c, i) => {
        const y = top + 10 + i * 20;
        root.appendChild(svg("rect", { x: W * 0.7, y: y - 10, width: 12, height: 12, class: `chart-s${i % 8}` }));
        root.appendChild(svg("text", { x: W * 0.7 + 18, y, class: "chart-label" }, `${c} (${total ? Math.round(Math.max(0, s.data[i]) / total * 100) : 0}%)`));
      });
      return root;
    }
    const all = data.series.flatMap((s) => s.data);
    let lo = Math.min(0, ...all), hi = Math.max(0, ...all);
    if (hi === lo) hi = lo + 1;
    const raw = (hi - lo) / 4;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const tick = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((t) => t >= raw) || raw;
    lo = Math.floor(lo / tick) * tick; hi = Math.ceil(hi / tick) * tick;
    const ticks = Math.round((hi - lo) / tick);
    const left = 52, right = W - 10, bottom = H - (data.series.length > 1 ? 44 : 26);
    const y = (v) => bottom - (v - lo) / (hi - lo) * (bottom - top);
    for (let i = 0; i <= ticks; i++) {
      const v = lo + tick * i;
      root.appendChild(svg("line", { x1: left, x2: right, y1: y(v), y2: y(v), class: "chart-grid" }));
      root.appendChild(svg("text", { x: left - 6, y: y(v) + 4, class: "chart-label", "text-anchor": "end" }, S.formatCode(v, tick < 1 ? "0.##" : "#,##0")));
    }
    const n = data.cats.length;
    const band = (right - left) / n;
    const step = Math.max(1, Math.ceil(n / 12));
    data.cats.forEach((c, i) => { if (i % step === 0) root.appendChild(svg("text", { x: left + band * (i + 0.5), y: bottom + 16, class: "chart-label", "text-anchor": "middle" }, String(c).slice(0, 14))); });
    if (def.type === "line") {
      data.series.forEach((s, k) => {
        const pts = s.data.map((v, i) => `${left + band * (i + 0.5)},${y(v)}`).join(" ");
        root.appendChild(svg("polyline", { points: pts, class: `chart-line chart-s${k % 8}` }));
        s.data.forEach((v, i) => root.appendChild(svg("circle", { cx: left + band * (i + 0.5), cy: y(v), r: 3, class: `chart-s${k % 8}` }, svg("title", null, `${s.name} · ${data.cats[i]}: ${S.numText(v)}`))));
      });
    } else {
      const m = data.series.length;
      const bw = Math.max(2, band * 0.8 / m);
      data.series.forEach((s, k) => s.data.forEach((v, i) => {
        const x = left + band * i + band * 0.1 + bw * k;
        const y0 = y(Math.max(0, v)), y1 = y(Math.min(0, v));
        root.appendChild(svg("rect", { x, y: y0, width: bw - 1, height: Math.max(1, y1 - y0), class: `chart-s${k % 8}` }, svg("title", null, `${s.name} · ${data.cats[i]}: ${S.numText(v)}`)));
      }));
    }
    root.appendChild(svg("line", { x1: left, x2: right, y1: y(0), y2: y(0), class: "chart-axis" }));
    if (data.series.length > 1) data.series.slice(0, 6).forEach((s, k) => {
      const x = left + k * ((right - left) / Math.min(6, data.series.length));
      root.appendChild(svg("rect", { x, y: H - 16, width: 10, height: 10, class: `chart-s${k % 8}` }));
      root.appendChild(svg("text", { x: x + 14, y: H - 7, class: "chart-label" }, String(s.name).slice(0, 18)));
    });
    return root;
  }
  D.drawChart = drawChart;

  // ---------------------------------------------------------------- conditional colours
  function condMarks(tab, value) {
    const marks = new Map();
    for (const rule of tab.cond || []) {
      const r = S.parseRange(rule.range);
      if (!r) continue;
      const cells = [];
      for (let row = r.r1; row <= Math.min(r.r2, MAXR - 1); row++) for (let c = r.c1; c <= r.c2; c++) {
        const v = value(tab, c, row);
        if (v !== null && v !== undefined && v !== "") cells.push([S.addr(c, row), v]);
      }
      let test;
      const nums = cells.filter(([, v]) => typeof v === "number");
      switch (rule.type) {
        case "gt": case "after": test = (v) => typeof v === "number" && v > rule.a; break;
        case "lt": case "before": test = (v) => typeof v === "number" && v < rule.a; break;
        case "between": { const lo = Math.min(rule.a, rule.b), hi = Math.max(rule.a, rule.b); test = (v) => typeof v === "number" && v >= lo && v <= hi; break; }
        case "contains": { const t = String(rule.a).toLowerCase(); test = (v) => S.toText(v).toLowerCase().includes(t); break; }
        case "top": case "bottom": {
          const sorted = nums.map(([, v]) => v).sort((a, b) => (rule.type === "top" ? b - a : a - b));
          const lim = sorted[Math.min(rule.n, sorted.length) - 1];
          test = (v) => typeof v === "number" && sorted.length && (rule.type === "top" ? v >= lim : v <= lim);
          break;
        }
        case "dup": {
          const counts = new Map();
          for (const [, v] of cells) { const k = typeof v === "string" ? "s" + v.toLowerCase() : "n" + S.toText(v); counts.set(k, (counts.get(k) || 0) + 1); }
          test = (v) => (counts.get(typeof v === "string" ? "s" + v.toLowerCase() : "n" + S.toText(v)) || 0) > 1;
          break;
        }
        default: test = () => false;
      }
      for (const [ref, v] of cells) if (!S.isErr(v) && test(v)) { const m = marks.get(ref) || {}; if (rule.fill && !m.fill) m.fill = rule.fill; if (rule.text && !m.text) m.text = rule.text; marks.set(ref, m); }
    }
    return marks;
  }

  // ================================================================ the editor
  D.editors.sheet = function sheetEditor(page, doc, ctx) {
    const me = D.state.me;
    // numberStyle: the person's own setting (Settings → You): how numbers look and how typed numbers are read
    const opts = { currency: doc.currency || (me.app && me.app.currency) || "EUR", locale: undefined,
      numStyle: (me.prefs && me.prefs.numberStyle) || "auto" };
    numStyle = opts.numStyle;
    // the cells changed since you last looked (§17.21), "Tab\tB4", highlighted for this visit
    const changedCells = new Set(Object.entries((doc.changedSince && doc.changedSince.cells) || {})
      .flatMap(([tab, refs]) => refs.map((ref) => tab + "\t" + ref)));
    const tz = me.app && me.app.timeZone;
    const st = {
      sheet: doc.sheet, base: clone(doc.sheet), etag: doc.etag, tab: 0,
      pending: new Map(), calc: new Map(), metaDirty: new Set(), shape: false,
      sel: { c1: 0, r1: 0, c2: 0, r2: 0 }, editing: null, filters: {}, undo: [], redo: [],
      saving: null, again: false, marks: null, clip: null, csvNote: false,
    };
    const csv = doc.format === "csv";
    const canEdit = !!doc.canEdit;
    let wb = S.workbook({ today: () => todayIn(tz) });
    const grids = () => st.sheet.tabs.filter((t) => t.kind === "grid");
    const cur = () => st.sheet.tabs[st.tab];
    function loadEngine() {
      wb = S.workbook({ today: () => todayIn(tz) });
      wb.setTabs(grids().map((t) => t.name));
      for (const t of grids()) {
        for (const [ref, cell] of Object.entries(t.cells)) if (cell.v !== null && cell.v !== undefined) wb.set(t.name, ref, cell.v, !!cell.q);
      }
      wb.recalc();
      st.marks = null;
    }
    function value(tab, c, r) {       // what a cell holds now (computed for formulas; Excel's value for "x" cells)
      const ref = S.addr(c, r);
      const cell = tab.cells[ref];
      if (!cell) return null;
      if (cell.x) return fromWire(cell.c ?? null);
      if (isFormulaCell(cell)) { const v = wb.value(tab.name, ref); return v === undefined ? null : v; }
      return cell.v === undefined ? null : cell.v;
    }
    loadEngine();

    // ------------------------------------------------------------ layout
    const head = D.editorHead ? D.editorHead(doc, ctx) : null;
    const status = head ? head.status : h("span");
    const setStatus = (t, kind) => { status.textContent = t; status.className = "save-state" + (kind ? " " + kind : ""); };
    const refBox = h("input", { class: "sheet-ref", id: "sheetRef", "aria-label": "Cell", value: "A1", maxlength: "40" });
    const fBtn = h("button", { class: "icon-btn sheet-fx", type: "button", id: "sheetFx", title: "Functions", "aria-label": "Functions" }, "ƒ");
    const bar = h("input", { class: "sheet-bar", id: "sheetBar", "aria-label": "Cell contents", spellcheck: "false", autocomplete: "off", readonly: !canEdit });
    const suggest = h("div", { class: "sheet-suggest", id: "sheetSuggest", role: "listbox", hidden: true });
    const barRow = h("div", { class: "sheet-barrow" }, refBox, fBtn, h("div", { class: "sheet-bar-wrap" }, bar, suggest));
    const tools = h("div", { class: "sheet-tools", id: "sheetTools" });
    const grid = h("div", { class: "sheet-wrap", id: "sheetGrid", tabindex: "0", role: "grid", "aria-label": "Cells" });
    const table = h("table", { class: "sheet-table" });
    grid.appendChild(table);
    const cellInput = h("input", { class: "sheet-cell-input", id: "sheetCellInput", hidden: true, spellcheck: "false", autocomplete: "off", "aria-label": "Edit cell" });
    grid.appendChild(cellInput);
    const tabsBar = h("div", { class: "sheet-tabs", id: "sheetTabs", role: "tablist" });
    const statusBar = h("div", { class: "sheet-status", id: "sheetStatus", role: "status", "aria-live": "polite" });
    const below = h("div", { class: "sheet-charts", id: "sheetCharts" });
    const notices = h("div", { class: "sheet-notices" });
    const chartPane = h("div", { class: "sheet-chart-tab", id: "sheetChartTab", hidden: true });
    // the secret hint (SPEC §3.3): a light check of the cells' text in the browser; nothing is sent
    const secretWarn = h("div", { class: "secret-warn", role: "status", hidden: true });
    const secretEl = me.app && me.app.secretHint ? h("div", { class: "secret-hint" }, h("span", { class: "hint" }, "Docs are plain files — keep passwords and card numbers in Household Vault."), secretWarn) : null;
    const checkSecret = debounce(() => {
      if (!secretEl || !D.looksSecret) return;
      const parts = [];
      for (const t of st.sheet.tabs) if (t.kind === "grid") for (const cell of Object.values(t.cells)) { if (typeof cell.v === "string" && !isFormulaCell(cell)) parts.push(cell.v); if (parts.length > 5000) break; }
      const what = D.looksSecret(parts.join("\n"));
      secretWarn.hidden = !what;
      secretWarn.textContent = what ? `⚠ This looks like it holds ${what}. Anyone with access to /share can read docs — Household Vault keeps it encrypted.` : "";
    }, 800);
    mount(page, head ? head.el : null, notices,
      h("div", { class: "sheet-editor" + (canEdit ? "" : " read-only") },
        barRow,
        tools, grid, chartPane, statusBar, tabsBar), below, secretEl);
    page.classList.add("sheet-page");

    // notices: read-only foreign file, .csv, viewers
    function drawNotices() {
      const out = [];
      if (doc.readOnly) {
        out.push(h("div", { class: "card warn-card sheet-notice", id: "sheetReadOnly" },
          h("strong", null, "Read only — made in another app"),
          h("p", null, "This .xlsx has things Household Docs can't keep, so saving here could lose them. It opens read only:"),
          h("ul", { class: "feature-list" }, doc.features.map((f) => h("li", null, f))),
          h("div", { class: "actions start" }, h("button", { class: "btn-primary", type: "button", id: "sheetEditCopy", onclick: async () => {
            try { const it = await api(`api/docs/${doc.id}/edit-copy`, { method: "POST" }); toast(`Saved a copy: ${it.name}`); D.openItem(it); } catch (e) { fail(e); }
          } }, "Save a copy to edit"))));
      } else if (csv && canEdit && lsGet("docs.csvNote." + doc.id) !== "1") {
        out.push(h("div", { class: "card sheet-notice", id: "sheetCsvNote" },
          h("p", null, "This is a .csv sheet: it keeps values and formulas only. Formats, widths, colours, charts, a totals row and more tabs aren't saved in a .csv."),
          h("div", { class: "actions start" },
            h("button", { class: "btn-primary", type: "button", id: "sheetConvert", onclick: convert }, "Convert to .xlsx"),
            h("button", { class: "btn-ghost", type: "button", onclick: () => { lsSet("docs.csvNote." + doc.id, "1"); drawNotices(); } }, "OK"))));
      }
      mount(notices, out);
    }
    async function convert() {
      if (!(await confirmDialog("Convert to .xlsx", "The sheet becomes an .xlsx file (the .csv is kept under History). Excel, LibreOffice and the app then keep formats, tabs and charts.", "Convert"))) return;
      try { await flush(); await api(`api/docs/${doc.id}/convert`, { method: "POST" }); toast("Converted to .xlsx"); D.render(); } catch (e) { fail(e); }
    }
    drawNotices();
    function csvBlocked() { if (!csv) return false; toast("A .csv sheet keeps values and formulas only — Convert to .xlsx to use this.", true); return true; }
    function roBlocked() { if (canEdit) return false; toast(doc.readOnly ? "This sheet is read only — Save a copy to edit." : "You can only view this sheet.", true); return true; }

    // ------------------------------------------------------------ the toolbar
    const fmtSel = h("select", { id: "sheetFormat", "aria-label": "Number format", title: "Number format" }, FORMATS.map(([v, l]) => h("option", { value: v }, l)));
    fmtSel.addEventListener("change", () => setFormat({ f: fmtSel.value === "general" ? null : fmtSel.value }));
    const tb = (id, label, title, fn, cls) => h("button", { class: "icon-btn sheet-tool" + (cls ? " " + cls : ""), type: "button", id, title, "aria-label": title, onclick: fn }, label);
    const boldBtn = tb("sheetBold", "B", "Bold (Ctrl+B)", () => toggle("b"), "bold");
    const redBtn = tb("sheetRed", "−1", "Negative numbers in red", () => toggle("red"), "red-neg");
    function drawTools() {
      const chartTab = cur() && cur().kind === "chart";
      barRow.hidden = chartTab;
      if (canEdit && csv) {
        mount(tools, tb("sheetSort", "⇅", "Sort", (e) => sortMenu(e.currentTarget)), tb("sheetFilter", "⏷", "Filter a column", () => filterDialog(rangeOf(st.sel).c1)),
          h("span", { class: "tool-sep" }), tb("sheetUndo", "↶", "Undo (Ctrl+Z)", undo), tb("sheetRedo", "↷", "Redo (Ctrl+Y)", redo),
          tb("sheetExport", "⬇", "Export", (e) => exportMenu(e.currentTarget)), tb("sheetPrint", "🖨", "Print", printSheet), tb("sheetFind", "🔍", "Find (Ctrl+F)", findDialog),
          h("button", { class: "btn-ghost btn-small", type: "button", id: "sheetConvertTool", onclick: convert }, "Convert to .xlsx"));
        return;
      }
      if (chartTab) {
        mount(tools, tb("sheetExport", "⬇", "Export", (e) => exportMenu(e.currentTarget)), tb("sheetPrint", "🖨", "Print", printSheet), tb("sheetFind", "🔍", "Find (Ctrl+F)", findDialog));
        return;
      }
      if (!canEdit) {
        mount(tools, h("span", { class: "chip" }, doc.readOnly ? "Read only" : "View only"),
          tb("sheetExport", "⬇", "Export", (e) => exportMenu(e.currentTarget)), tb("sheetPrint", "🖨", "Print", printSheet), tb("sheetFind", "🔍", "Find (Ctrl+F)", findDialog));
        return;
      }
      mount(tools, fmtSel,
        tb("sheetDecLess", ".0←", "Fewer decimals", () => decimals(-1)), tb("sheetDecMore", "→.00", "More decimals", () => decimals(1)),
        boldBtn, tb("sheetLeft", "⇤", "Align left", () => align("left")), tb("sheetCenter", "↔", "Align centre", () => align("center")), tb("sheetRight", "⇥", "Align right", () => align("right")),
        redBtn, h("span", { class: "tool-sep" }),
        tb("sheetTotals", "Σ", "Totals row", toggleTotals), tb("sheetFreeze", "❄", "Freeze rows / columns", (e) => freezeMenu(e.currentTarget)),
        tb("sheetSort", "⇅", "Sort", (e) => sortMenu(e.currentTarget)), tb("sheetFilter", "⏷", "Filter a column", () => filterDialog(rangeOf(st.sel).c1)),
        tb("sheetChart", "📈", "Chart", () => chartDialog()), tb("sheetCond", "🎨", "Conditional colours", condDialog),
        h("span", { class: "tool-sep" }),
        tb("sheetUndo", "↶", "Undo (Ctrl+Z)", undo), tb("sheetRedo", "↷", "Redo (Ctrl+Y)", redo),
        tb("sheetExport", "⬇", "Export", (e) => exportMenu(e.currentTarget)), tb("sheetPrint", "🖨", "Print", printSheet), tb("sheetFind", "🔍", "Find (Ctrl+F)", findDialog));
    }
    drawTools();

    // ------------------------------------------------------------ changing cells
    function baseCell(tabName, ref) {
      const t = st.base.tabs.find((x) => x.name.toLowerCase() === tabName.toLowerCase());
      return t && t.kind === "grid" ? (t.cells[ref] || null) : null;
    }
    /** changes: [{tab (name), ref, cell (null = empty)}]. Records undo, pending, recalculates. */
    function setCells(changes, { record = true } = {}) {
      if (!changes.length) return;
      const undoBatch = [];
      const engine = [];
      for (const ch of changes) {
        const t = st.sheet.tabs.find((x) => x.name === ch.tab);
        if (!t) continue;
        const before = t.cells[ch.ref] ? clone(t.cells[ch.ref]) : null;
        let cell = ch.cell ? Object.assign({}, ch.cell) : null;
        if (cell) { for (const k of Object.keys(cell)) if (cell[k] === null || cell[k] === undefined || cell[k] === false || (k === "f" && cell[k] === "general")) delete cell[k]; delete cell.x; delete cell.c; delete cell.t; if (!Object.keys(cell).length) cell = null; }
        if (contentOf(before) === contentOf(cell)) continue;
        if (cell) t.cells[ch.ref] = cell; else delete t.cells[ch.ref];
        undoBatch.push({ tab: ch.tab, ref: ch.ref, before, after: cell ? clone(cell) : null });
        const k = cellKey(ch.tab, ch.ref);
        if (!st.pending.has(k)) st.pending.set(k, { tab: ch.tab, ref: ch.ref, was: baseCell(ch.tab, ch.ref) });
        st.calc.delete(k);
        const bv = before ? before.v : null, av = cell ? cell.v : null;
        if (bv !== av || !!(before && before.q) !== !!(cell && cell.q)) engine.push([ch.tab, ch.ref, av === undefined ? null : av, !!(cell && cell.q)]);
      }
      if (!undoBatch.length) return;
      if (record) { st.undo.push({ cells: undoBatch }); if (st.undo.length > 200) st.undo.shift(); st.redo = []; }
      if (engine.length) {
        const changed = wb.update(engine);
        // dependent formulas: their new values go into the file too (so other apps show them)
        const keys = changed === null ? allFormulaKeys() : changed;
        for (const key of keys) {
          const i = key.indexOf("!");
          const tn = key.slice(0, i), ref = key.slice(i + 1);
          const k = cellKey(tn, ref);
          if (!st.pending.has(k)) st.calc.set(k, { tab: tn, ref });
        }
      }
      st.marks = null;
      changed();
    }
    function allFormulaKeys() {
      const out = [];
      for (const t of grids()) for (const [ref, cell] of Object.entries(t.cells)) if (isFormulaCell(cell)) out.push(t.name + "!" + ref);
      return out;
    }
    function changed() { setStatus("Unsaved changes…"); draw(); soon(); checkSecret(); }
    function setMeta(fn) {           // widths, freeze, totals, colour rules, charts of the current tab
      if (roBlocked()) return;
      const t = cur();
      const before = clone({ cols: t.cols, freeze: t.freeze, totals: t.totals, cond: t.cond, charts: t.charts });
      fn(t);
      st.undo.push({ meta: { tab: t.name, before } }); st.redo = [];
      st.metaDirty.add(t.name);
      st.marks = null;
      changed();
    }
    function setShape(fn, label) {    // rows/columns inserted or deleted, sort, tabs: the whole sheet is saved
      if (roBlocked()) return;
      const before = clone(st.sheet);
      const beforeTab = st.tab;
      fn();
      st.undo.push({ shape: before, tab: beforeTab }); st.redo = [];
      st.shape = true;
      loadEngine();
      if (label) toast(label);
      changed();
      drawTabs();
    }
    function undo() {
      const u = st.undo.pop();
      if (!u) return;
      applyUndo(u, true);
    }
    function redo() {
      const u = st.redo.pop();
      if (!u) return;
      applyUndo(u, false);
    }
    function applyUndo(u, isUndo) {
      if (u.cells) {
        setCells(u.cells.map((c) => ({ tab: c.tab, ref: c.ref, cell: isUndo ? c.before : c.after })), { record: false });
        (isUndo ? st.redo : st.undo).push(u);
      } else if (u.meta) {
        const t = st.sheet.tabs.find((x) => x.name === u.meta.tab);
        if (!t) return;
        const now = clone({ cols: t.cols, freeze: t.freeze, totals: t.totals, cond: t.cond, charts: t.charts });
        Object.assign(t, clone(u.meta.before));
        (isUndo ? st.redo : st.undo).push({ meta: { tab: t.name, before: now } });
        st.metaDirty.add(t.name); st.marks = null; changed();
      } else if (u.shape) {
        const now = clone(st.sheet);
        const nowTab = st.tab;
        st.sheet = u.shape; st.tab = Math.min(u.tab, st.sheet.tabs.length - 1);
        (isUndo ? st.redo : st.undo).push({ shape: now, tab: nowTab });
        st.shape = true; loadEngine(); drawTabs(); changed();
      }
    }

    // ------------------------------------------------------------ saving
    const soon = debounce(() => save(), 1000);
    function hasWork() { return st.pending.size || st.calc.size || st.metaDirty.size || st.shape; }
    function computedFor(tabName, ref, cell) {
      if (!isFormulaCell(cell) || cell.x) return cell.c;
      return wire(wb.value(tabName, ref));
    }
    function payloadSheet() {
      const out = clone(st.sheet);
      for (const t of out.tabs) if (t.kind === "grid") for (const [ref, cell] of Object.entries(t.cells)) {
        if (isFormulaCell(cell)) cell.c = computedFor(t.name, ref, cell);
      }
      return out;
    }
    function metaOf(t) { return { name: t.name, cols: t.cols || {}, freeze: t.freeze || { r: 0, c: 0 }, totals: t.totals || null, cond: t.cond || [], charts: t.charts || [] }; }
    async function save(o = {}) {
      if (!canEdit || !hasWork()) return;
      if (st.saving) { st.again = true; return st.saving; }
      let body;
      const sentPending = st.pending, sentCalc = st.calc, sentMeta = st.metaDirty, sentShape = st.shape;
      st.pending = new Map(); st.calc = new Map(); st.metaDirty = new Set(); st.shape = false;
      if (sentShape) body = { etag: st.etag, sheet: payloadSheet() };
      else {
        const cells = [];
        for (const p of sentPending.values()) {
          const t = st.sheet.tabs.find((x) => x.name === p.tab);
          const now = t && t.cells[p.ref] ? clone(t.cells[p.ref]) : null;
          if (now && isFormulaCell(now)) now.c = computedFor(p.tab, p.ref, now);
          cells.push({ tab: p.tab, ref: p.ref, was: p.was, now });
        }
        for (const p of sentCalc.values()) {
          const t = st.sheet.tabs.find((x) => x.name === p.tab);
          const cell = t && t.cells[p.ref];
          if (!cell || !isFormulaCell(cell)) continue;
          const now = Object.assign(clone(cell), { c: computedFor(p.tab, p.ref, cell) });
          cells.push({ tab: p.tab, ref: p.ref, was: baseCell(p.tab, p.ref), now });
        }
        body = { etag: st.etag, cells };
        st.lastSent = new Map(cells.map((c) => [cellKey(c.tab, c.ref), c]));
        if (sentMeta.size) body.tabs = [...sentMeta].map((n) => st.sheet.tabs.find((t) => t.name === n)).filter((t) => t && t.kind === "grid").map(metaOf);
      }
      setStatus("Saving…");
      st.saving = (async () => {
        try {
          const r = await api(`api/docs/${doc.id}`, { method: "PATCH", body, keepalive: !!o.keepalive });
          st.etag = r.etag;
          if (r.sheet) adopt(r.sheet, r.by ? `Updated — ${r.by} changed it too` : (r.outside ? "Updated — it was changed outside the app" : null));
          else {
            if (sentShape) st.base = clone(body.sheet);
            else {
              for (const c of body.cells) {
                const t = st.base.tabs.find((x) => x.name === c.tab);
                if (t) { if (c.now) t.cells[c.ref] = clone(c.now); else delete t.cells[c.ref]; }
              }
              for (const m of body.tabs || []) { const t = st.base.tabs.find((x) => x.name === m.name); if (t) Object.assign(t, clone(m)); }
            }
          }
          setStatus(hasWork() ? "Unsaved changes…" : "Saved");
        } catch (e) {
          if (e.status === 409 && e.detail && e.detail.readOnly) { setStatus("Read only", "warn"); toast(e.message, true); D.render(); return; }
          if (e.status === 409 && e.detail && e.detail.conflicts) {
            st.etag = e.detail.etag || st.etag;
            if (e.detail.sheet) adopt(e.detail.sheet, null);
            setStatus("Saved");
            if (e.detail.tabsNotSaved && e.detail.tabsNotSaved.length) toast("Someone changed this sheet meanwhile, so your column widths, frozen panes, totals, colour rules or charts weren't saved — please do them again.", true, { ms: 8000 });
            if (e.detail.conflicts.length) conflictsDialog(e.detail.conflicts);
            return;
          }
          if (e.status === 409 && e.detail && e.detail.stale && e.detail.sheet) {
            st.etag = e.detail.etag; adopt(e.detail.sheet, null); setStatus("Saved"); toast(e.message, true, { ms: 8000 }); return;
          }
          // put the work back for the next try
          for (const [k, v] of sentPending) if (!st.pending.has(k)) st.pending.set(k, v);
          for (const [k, v] of sentCalc) if (!st.calc.has(k)) st.calc.set(k, v);
          for (const n of sentMeta) st.metaDirty.add(n);
          if (sentShape) st.shape = true;
          if (!e.status) { setStatus("Offline — will retry", "warn"); clearTimeout(st.retry); st.retry = setTimeout(() => save(), 5000); }
          else { setStatus("Not saved", "warn"); fail(e); }
        } finally {
          st.saving = null;
          if (st.again) { st.again = false; if (hasWork()) save(); }
        }
      })();
      return st.saving;
    }
    async function flush() { if (st.saving) await st.saving; if (hasWork()) await save(); }
    /** The server's sheet (others' changes) becomes ours; changes made here since that save are put back on top. */
    function adopt(serverSheet, message) {
      const keepTabName = cur() ? cur().name : null;
      const mine = [];
      for (const p of st.pending.values()) { const t = st.sheet.tabs.find((x) => x.name === p.tab); mine.push({ tab: p.tab, ref: p.ref, cell: t && t.cells[p.ref] ? clone(t.cells[p.ref]) : null }); }
      const meta = [...st.metaDirty].map((n) => st.sheet.tabs.find((t) => t.name === n)).filter(Boolean).map((t) => clone(metaOf(t)));
      st.sheet = clone(serverSheet);
      st.base = clone(serverSheet);
      for (const m of mine) { const t = st.sheet.tabs.find((x) => x.name === m.tab); if (t) { if (m.cell) t.cells[m.ref] = m.cell; else delete t.cells[m.ref]; } }
      for (const m of meta) { const t = st.sheet.tabs.find((x) => x.name === m.name); if (t) Object.assign(t, m); }
      st.shape = false;
      const ti = st.sheet.tabs.findIndex((t) => t.name === keepTabName);
      st.tab = ti >= 0 ? ti : 0;
      st.undo = []; st.redo = [];
      loadEngine(); drawTabs(); draw();
      if (message) toast(message);
    }
    function conflictsDialog(conflicts) {
      const rows = conflicts.map((c) => ({ c }));
      const body = h("div", null,
        h("p", null, conflicts.length === 1 ? "Someone changed a cell you changed, while you were editing:" : "Someone changed cells you changed, while you were editing:"),
        h("div", { class: "item-list" }, rows.map(({ c }) => h("div", { class: "item-row conflict-row" },
          h("div", { class: "row-main static" }, h("span", { class: "row-text" }, h("span", { class: "row-name" }, `${c.tab} › ${c.ref}`),
            h("span", { class: "row-meta" }, c.gone ? c.message : `Now: ${c.theirs ? inputText(c.theirs) || "(empty)" : "(empty)"}`)))))),
        h("p", { class: "hint" }, "Their version is showing now. Keep mine puts your version back (theirs stays under History)."));
      const m = openModal("Changed while you were editing", h("div", null, body, h("div", { class: "actions" },
        h("button", { class: "btn-secondary", type: "button", id: "useTheirsCells", onclick: () => m.close() }, "Use theirs"),
        h("button", { class: "btn-primary", type: "button", id: "keepMineCells", onclick: () => {
          m.close();
          const back = [];
          for (const { c } of rows) { if (c.gone) continue; const mine = st.lastSent && st.lastSent.get(cellKey(c.tab, c.ref)); back.push({ tab: c.tab, ref: c.ref, cell: mine ? mine.now : null }); }
          setCells(back);
        } }, "Keep mine"))), { focus: false });
    }

    // ------------------------------------------------------------ drawing the grid
    let first = 0;
    let lastDraw = null;
    function dims() {
      const t = cur();
      const used = usedOf(t);
      // like a spreadsheet: at least 1 000 rows and A–Z, more past the last cell used or the selection
      const sel = st.sel || { r1: 0, r2: 0, c1: 0, c2: 0 };
      // (room around the active cell; a selection's far end is only included, so "select all" doesn't grow it)
      const rows = Math.min(MAXR, Math.max(used.rows + 100, sel.r1 + 100, (sel.r2 ?? 0) + 1, MIN_ROWS));
      const cols = Math.min(MAXC, Math.max(used.cols + 5, sel.c1 + 5, (sel.c2 ?? 0) + 1, MIN_COLS));
      return { rows, cols, used };
    }
    function widthOf(t, c) { return (t.cols && t.cols[S.colName(c)]) || DEFAULT_W; }
    function visibleRows(t, total) {
      const f = st.filters[t.name];
      if (!f) return null;
      const out = [];
      for (let r = 0; r < total; r++) {
        if (r === 0) { out.push(r); continue; }
        const v = value(t, f.col, r);
        const shown = S.format(v, t.cells[S.addr(f.col, r)], opts).text;
        if (f.allowed.has(shown)) out.push(r);
      }
      return out;
    }
    function draw() {
      if (st.inPointer) {          // never rebuild the cell under a pressed pointer: next frame instead
        if (!st.drawSoon) { st.drawSoon = true; requestAnimationFrame(() => { st.drawSoon = false; draw(); }); }
        return;
      }
      const t = cur();
      if (t.kind === "chart") { grid.hidden = true; chartPane.hidden = false; drawChartTab(t); drawStatus(); drawBelow(); return; }
      grid.hidden = false; chartPane.hidden = true;
      const { rows, cols } = dims();
      const fr = Math.min((t.freeze && t.freeze.r) || 0, rows), fc = Math.min((t.freeze && t.freeze.c) || 0, cols);
      const vis = visibleRows(t, rows);
      const total = vis ? vis.length : rows;
      const viewH = grid.clientHeight || 480;
      const per = Math.ceil(viewH / RH) + 12;
      first = Math.max(fr, Math.floor(grid.scrollTop / RH) - 6);
      const last = Math.min(total, first + per);
      if (!st.marks) st.marks = condMarks(t, value);
      const sel = rangeOf(st.sel);
      const lefts = [RHEAD];
      for (let c = 0; c < cols; c++) lefts.push(lefts[c] + widthOf(t, c));
      const colgroup = h("colgroup", null, h("col", { class: "rh-col" }), Array.from({ length: cols }, (_, c) => { const el = h("col"); el.style.width = widthOf(t, c) + "px"; return el; }));
      const headRow = h("tr", null, h("th", { class: "corner", title: "Select everything", onclick: () => { st.sel = { c1: 0, r1: 0, c2: cols - 1, r2: rows - 1 }; draw(); } }),
        Array.from({ length: cols }, (_, c) => {
          const th = h("th", { class: "ch" + (c >= sel.c1 && c <= sel.c2 ? " on" : "") + (c < fc ? " frozen" : "") + (st.filters[t.name] && st.filters[t.name].col === c ? " filtered" : ""), dataset: { c: String(c) } }, S.colName(c),
            h("button", { type: "button", class: "col-menu", tabindex: "-1", dataset: { c: String(c) }, title: `Column ${S.colName(c)}: sort, filter, total …`, "aria-label": `Column ${S.colName(c)} menu` }, "▾"),
            canEdit ? h("span", { class: "col-resize", dataset: { c: String(c) }, "aria-hidden": "true" }) : null);
          if (c < fc) th.style.left = lefts[c] + "px";
          return th;
        }));
      const body = [];
      const rowEl = (r, frozenIdx) => {
        const tr = h("tr", { class: frozenIdx !== null ? "frozen-row" : null, dataset: { r: String(r) } });
        tr.appendChild(h("th", { class: "rh" + (r >= sel.r1 && r <= sel.r2 ? " on" : ""), dataset: { r: String(r) } }, String(r + 1)));
        for (let c = 0; c < cols; c++) tr.appendChild(cellEl(t, c, r, c < fc ? lefts[c] : null, sel));
        if (frozenIdx !== null) for (const el of tr.children) el.style.top = (RH + frozenIdx * RH) + "px";
        return tr;
      };
      for (let i = 0; i < fr && i < total; i++) body.push(rowEl(vis ? vis[i] : i, i));
      const topPad = (first - fr) * RH;
      if (topPad > 0) body.push(h("tr", { class: "pad" }, h("td", { colspan: String(cols + 1) })));
      for (let i = first; i < last; i++) body.push(rowEl(vis ? vis[i] : i, null));
      const bottomPad = (total - last) * RH;
      if (bottomPad > 0) body.push(h("tr", { class: "pad" }, h("td", { colspan: String(cols + 1) })));
      const tbody = h("tbody", null, body);
      const pads = tbody.querySelectorAll("tr.pad > td");
      if (topPad > 0) pads[0].style.height = topPad + "px";
      if (bottomPad > 0) pads[pads.length - 1].style.height = bottomPad + "px";
      const foot = t.totals && Object.keys(t.totals).length ? totalsRow(t, cols, lefts, fc) : null;
      table.replaceChildren(...[colgroup, h("thead", null, headRow), tbody, foot].filter(Boolean));   // no totals: nothing (not "null")
      table.style.width = lefts[cols] + "px";
      lastDraw = { cols, rows, fr, fc, lefts, vis, total };
      placeInput();
      drawBar();
      drawStatus();
      drawBelow();
    }
    /** Only the selection changed: move the highlight without rebuilding the grid (a click must not replace
     *  the cell under the pointer). */
    function paintSel() {
      if (!lastDraw || cur().kind !== "grid") { draw(); return; }
      const sel = rangeOf(st.sel);
      for (const el of table.querySelectorAll("td.sel, td.active")) el.classList.remove("sel", "active");
      if (!st.editing || !st.editing.point) for (const el of table.querySelectorAll("td.point")) el.classList.remove("point");
      for (const el of table.querySelectorAll("th.on")) el.classList.remove("on");
      for (const td of table.querySelectorAll("tbody td.cell")) {
        const c = +td.dataset.c, r = +td.dataset.r;
        if (c >= sel.c1 && c <= sel.c2 && r >= sel.r1 && r <= sel.r2) td.classList.add("sel");
        if (c === st.sel.c1 && r === st.sel.r1) td.classList.add("active");
      }
      for (const th of table.querySelectorAll("thead th.ch")) if (+th.dataset.c >= sel.c1 && +th.dataset.c <= sel.c2) th.classList.add("on");
      for (const th of table.querySelectorAll("tbody th.rh")) if (+th.dataset.r >= sel.r1 && +th.dataset.r <= sel.r2) th.classList.add("on");
      drawBar();
      drawStatus();
    }
    function cellEl(t, c, r, left, sel) {
      const ref = S.addr(c, r);
      const cell = t.cells[ref];
      const v = cell ? value(t, c, r) : null;
      const shown = cell ? S.format(v, cell, opts) : { text: "" };
      const cls = ["cell"];
      if (cell) {
        const al = cell.al || (typeof v === "number" ? "right" : (typeof v === "boolean" || S.isErr(v)) ? "center" : null);
        if (al) cls.push("al-" + al);
        if (cell.b) cls.push("b");
        if (shown.negative) cls.push("neg");
        if (shown.error) cls.push("err");
        if (cell.x) cls.push("excel");
        const mk = st.marks && st.marks.get(ref);
        if (mk) { if (mk.fill) cls.push("cf-fill-" + mk.fill); if (mk.text) cls.push("cf-text-" + mk.text); }
      }
      if (c >= sel.c1 && c <= sel.c2 && r >= sel.r1 && r <= sel.r2) cls.push("sel");
      if (c === st.sel.c1 && r === st.sel.r1) cls.push("active");
      if (left !== null) cls.push("frozen");
      if (changedCells.has(t.name + "\t" + ref)) cls.push("changed-cell");     // §17.21: changed since you looked
      const td = h("td", { class: cls.join(" "), dataset: { c: String(c), r: String(r) },
        title: shown.error ? `${v.code} — ${v.why}` : (cell && cell.x ? "Calculated by Excel — the app can't work this formula out" : null) }, shown.text);
      if (left !== null) td.style.left = left + "px";
      return td;
    }
    function totalOf(t, col, fn) {
      const xs = [];
      for (const [ref, cell] of Object.entries(t.cells)) {
        const p = S.parseRef(ref);
        if (!p || p.c !== col) continue;
        const v = value(t, p.c, p.r);
        if (typeof v === "number" && isFinite(v)) xs.push(v);
      }
      if (fn === "COUNT") return xs.length;
      if (!xs.length) return fn === "AVERAGE" ? S.ERR["#DIV/0!"] : 0;
      if (fn === "SUM") return xs.reduce((a, b) => a + b, 0);
      if (fn === "AVERAGE") return xs.reduce((a, b) => a + b, 0) / xs.length;
      return fn === "MIN" ? Math.min(...xs) : Math.max(...xs);
    }
    function columnFormat(t, col) {
      for (const [ref, cell] of Object.entries(t.cells)) { const p = S.parseRef(ref); if (p && p.c === col && cell.f && cell.f !== "text" && cell.f !== "date" && cell.f !== "datetime") return cell; }
      return null;
    }
    function totalsRow(t, cols, lefts, fc) {
      const tr = h("tr", { class: "totals-row", id: "sheetTotalsRow" }, h("th", { class: "rh", title: "Totals" }, "Σ"));
      for (let c = 0; c < cols; c++) {
        const fn = t.totals[S.colName(c)];
        let text = "";
        if (fn) { const v = totalOf(t, c, fn); text = S.format(v, fn === "COUNT" ? null : columnFormat(t, c), opts).text; }
        const td = h("td", { class: "cell total" + (fn ? " al-right" : "") + (c < fc ? " frozen" : ""), dataset: { c: String(c) }, title: fn ? `${fn} of column ${S.colName(c)} — tap to change` : "Tap to add a total" },
          fn ? h("span", { class: "total-fn" }, fn.slice(0, 3)) : null, text);
        if (c < fc) td.style.left = lefts[c] + "px";
        if (canEdit) td.addEventListener("click", (e) => totalMenu(e.currentTarget, c));
        tr.appendChild(td);
      }
      return h("tfoot", null, tr);
    }
    function totalMenu(anchor, col) {
      if (roBlocked() || csvBlocked()) return;
      const letter = S.colName(col);
      D.menu(anchor, [...TOTALS.map((fn) => ({ id: "total-" + fn, label: fn, run: () => setMeta((t) => { t.totals = Object.assign({}, t.totals || {}, { [letter]: fn }); }) })),
        "-", { id: "total-none", label: "No total", run: () => setMeta((t) => { const x = Object.assign({}, t.totals || {}); delete x[letter]; t.totals = Object.keys(x).length ? x : null; }) }]);
    }
    function toggleTotals() {
      if (csvBlocked()) return;
      const t = cur();
      if (t.totals && Object.keys(t.totals).length) { setMeta((x) => { x.totals = null; }); return; }
      setMeta((x) => {   // a SUM under every column with numbers
        const used = usedOf(x);
        const tot = {};
        for (let c = 0; c < used.cols; c++) {
          let nums = 0;
          for (let r = 1; r < used.rows; r++) if (typeof value(x, c, r) === "number") nums++;
          if (nums) tot[S.colName(c)] = "SUM";
        }
        x.totals = Object.keys(tot).length ? tot : { [S.colName(rangeOf(st.sel).c1)]: "SUM" };
      });
    }
    grid.addEventListener("scroll", () => {
      if (st.rafPending) return;
      st.rafPending = true;
      requestAnimationFrame(() => { st.rafPending = false; const f = Math.max(lastDraw ? lastDraw.fr : 0, Math.floor(grid.scrollTop / RH) - 6); if (f !== first) draw(); else placeInput(); });
    });

    // ------------------------------------------------------------ the bar, status, charts below
    function activeCell() { return cur().cells[S.addr(st.sel.c1, st.sel.r1)] || null; }
    function drawBar() {
      const t = cur();
      if (t.kind !== "grid") return;
      const r = rangeOf(st.sel);
      if (document.activeElement !== refBox) refBox.value = rangeText(r);
      if (!st.editing) bar.value = inputText(activeCell());
      const cell = activeCell();
      fmtSel.value = (cell && cell.f) || "general";
      boldBtn.classList.toggle("on", !!(cell && cell.b));
      redBtn.classList.toggle("on", !!(cell && cell.red));
    }
    function drawStatus() {
      const t = cur();
      if (t.kind !== "grid") { mount(statusBar, h("span", { class: "hint" }, `Chart of ${t.chart.source.tab} › ${t.chart.source.range}`)); return; }
      const r = rangeOf(st.sel);
      const cell = activeCell();
      const v = cell ? value(t, st.sel.c1, st.sel.r1) : null;
      const parts = [];
      if (r.c1 !== r.c2 || r.r1 !== r.r2) {
        const xs = [];
        let filled = 0;
        const rr2 = Math.min(r.r2, usedOf(t).rows);
        for (let row = r.r1; row <= rr2; row++) for (let c = r.c1; c <= r.c2; c++) { const x = value(t, c, row); if (x !== null && x !== "") filled++; if (typeof x === "number" && isFinite(x)) xs.push(x); }
        if (xs.length) {
          const sum = xs.reduce((a, b) => a + b, 0);
          const f = columnFormat(t, r.c1);
          const fmt = (n) => S.format(n, f, opts).text;
          parts.push(`Sum ${fmt(sum)}`, `Average ${fmt(sum / xs.length)}`, `Count ${xs.length}`, `Min ${fmt(Math.min(...xs))}`, `Max ${fmt(Math.max(...xs))}`);
        } else parts.push(`${filled} filled`);
      } else if (S.isErr(v)) parts.push(`${v.code}: ${v.why}`);
      else if (cell && cell.x) parts.push("Calculated by Excel — this formula uses something the app can't work out.");
      else if (isFormulaCell(cell)) { const tc = wb.tabNamed(t.name); const ec = tc && tc.cells.get(st.sel.r1 * S.MAXC + st.sel.c1); if (ec && ec.syntax) parts.push("Formula problem: " + ec.syntax); }
      mount(statusBar, parts.map((p, i) => h("span", { class: i === 0 && S.isErr(v) ? "status-err" : "status-part" }, p)));
    }
    function drawBelow() {
      const t = cur();
      const charts = t.kind === "grid" ? (t.charts || []) : [];
      mount(below, charts.map((ch) => h("div", { class: "card chart-card", dataset: { chart: ch.id } },
        h("div", { class: "chart-head" }, h("span", { class: "hint" }, `${({ bar: "Bar", line: "Line", pie: "Pie" })[ch.type]} chart of ${ch.range}`),
          canEdit && !csv ? h("button", { class: "icon-btn", type: "button", "aria-label": "Chart options", title: "Chart options", onclick: (e) => D.menu(e.currentTarget, [
            { id: "chart-edit", label: "Change…", run: () => chartDialog(ch) },
            { id: "chart-del", label: "Remove", danger: true, run: () => setMeta((x) => { x.charts = x.charts.filter((y) => y.id !== ch.id); }) }]) }, "⋯") : null),
        drawChart(ch, chartData(t, value, ch.range)))));
    }
    function drawChartTab(t) {
      const src = st.sheet.tabs.find((x) => x.kind === "grid" && x.name.toLowerCase() === t.chart.source.tab.toLowerCase());
      mount(chartPane, src ? drawChart(t.chart, chartData(src, value, t.chart.source.range), { w: 800, h: 420 }) : h("div", { class: "empty" }, "The tab this chart shows isn't there any more."),
        canEdit ? h("div", { class: "actions start" }, h("button", { class: "btn-ghost btn-small", type: "button", onclick: () => chartDialog(null, t) }, "Change chart…")) : null);
    }

    // ------------------------------------------------------------ tabs
    function drawTabs() {
      const btns = st.sheet.tabs.map((t, i) => {
        const b = h("button", { class: "sheet-tab" + (i === st.tab ? " active" : ""), type: "button", role: "tab", "aria-selected": i === st.tab ? "true" : "false", dataset: { tab: t.name },
          onclick: () => { if (st.tab === i) { if (canEdit && !csv) tabMenu(b, i); return; } commitEdit(); st.tab = i; st.sel = { c1: 0, r1: 0, c2: 0, r2: 0 }; st.marks = null; grid.scrollTop = 0; drawTabs(); draw(); } },
          t.kind === "chart" ? "📈 " : "", t.name);
        b.addEventListener("contextmenu", (e) => { if (canEdit && !csv) { e.preventDefault(); tabMenu(b, i); } });
        return b;
      });
      drawTools();
      mount(tabsBar, btns, canEdit && !csv ? h("button", { class: "sheet-tab add", type: "button", id: "sheetAddTab", title: "Add a tab", "aria-label": "Add a tab", onclick: addTab }, "+") : null);
    }
    function uniqueTabName(base) {
      let n = 1, name;
      const taken = new Set(st.sheet.tabs.map((t) => t.name.toLowerCase()));
      do { name = `${base}${n++}`; } while (taken.has(name.toLowerCase()));
      return name;
    }
    function checkTabName(name, except) {
      const n = (name || "").trim();
      if (!n) return "A tab needs a name.";
      if (n.length > 31) return "A tab name can be at most 31 characters.";
      if (/[:\\/?*[\]]/.test(n) || n.startsWith("'") || n.endsWith("'")) return "A tab name can't contain : \\ / ? * [ ] or start or end with '.";
      if (n.toLowerCase() === "history") return "“History” is a name Excel keeps for itself.";
      if (st.sheet.tabs.some((t, i) => i !== except && t.name.toLowerCase() === n.toLowerCase())) return "There is a tab with that name already.";
      return null;
    }
    function addTab() {
      if (csvBlocked() || roBlocked()) return;
      if (st.sheet.tabs.length >= MAXTABS) { toast(`A sheet can have at most ${MAXTABS} tabs.`, true); return; }
      setShape(() => {
        st.sheet.tabs.push({ name: uniqueTabName("Sheet"), kind: "grid", cells: {}, cols: {}, freeze: { r: 0, c: 0 }, totals: null, cond: [], charts: [] });
        st.tab = st.sheet.tabs.length - 1;
        st.sel = { c1: 0, r1: 0, c2: 0, r2: 0 };
      });
    }
    function tabMenu(anchor, i) {
      const t = st.sheet.tabs[i];
      D.menu(anchor, [
        { id: "tab-rename", label: "Rename…", run: async () => {
          const name = await D.askName("Rename tab", "Tab name", t.name, "Rename");
          if (!name || name === t.name) return;
          const err = checkTabName(name, i);
          if (err) { toast(err, true); return; }
          setShape(() => {
            const old = t.name;
            for (const g of st.sheet.tabs) {
              if (g.kind === "grid") for (const cell of Object.values(g.cells)) if (isFormulaCell(cell)) cell.v = S.renameTab(cell.v, old, name);
              if (g.kind === "chart" && g.chart.source.tab.toLowerCase() === old.toLowerCase()) g.chart.source.tab = name;
            }
            t.name = name;
          }, "Renamed — formulas that use it were updated");
        } },
        { id: "tab-left", label: "Move left", run: () => { if (i > 0) setShape(() => { st.sheet.tabs.splice(i - 1, 0, st.sheet.tabs.splice(i, 1)[0]); st.tab = i - 1; }); } },
        { id: "tab-right", label: "Move right", run: () => { if (i < st.sheet.tabs.length - 1) setShape(() => { st.sheet.tabs.splice(i + 1, 0, st.sheet.tabs.splice(i, 1)[0]); st.tab = i + 1; }); } },
        "-",
        { id: "tab-delete", label: "Delete tab…", danger: true, run: async () => {
          if (grids().length === 1 && t.kind === "grid") { toast("A sheet needs at least one tab with cells.", true); return; }
          if (!(await confirmDialog("Delete tab", `Delete the tab “${t.name}”? Formulas that use it will show #REF!. (Undo brings it back; the file's History keeps earlier versions.)`, "Delete", true))) return;
          setShape(() => { st.sheet.tabs.splice(i, 1); st.sheet.tabs = st.sheet.tabs.filter((x) => x.kind !== "chart" || st.sheet.tabs.some((g) => g.kind === "grid" && g.name.toLowerCase() === x.chart.source.tab.toLowerCase())); st.tab = Math.max(0, Math.min(i, st.sheet.tabs.length - 1)); });
        } },
      ]);
    }

    // ------------------------------------------------------------ selection and the mouse
    function cellAt(target) {
      const td = target.closest && target.closest("td.cell");
      if (!td || !td.dataset.r) return null;
      return { c: +td.dataset.c, r: +td.dataset.r };
    }
    let dragging = false;
    let pressTimer = null;
    grid.addEventListener("pointerdown", (e) => {
      st.inPointer = true;
      try { onPointerDown(e); } finally { st.inPointer = false; }
    });
    function onPointerDown(e) {
      if (e.target === cellInput || e.button === 2) return;
      const rs = e.target.closest(".col-resize");
      if (rs) { startResize(e, +rs.dataset.c); return; }
      if (e.target.closest(".col-menu")) return;                  // its click opens the column's menu
      const ch = e.target.closest("th.ch"), rh = e.target.closest("th.rh");
      if (ch || rh) {
        commitEdit();
        const { rows, cols } = lastDraw;
        if (ch) { const c = +ch.dataset.c; st.sel = e.shiftKey ? { c1: st.sel.c1, r1: 0, c2: c, r2: rows - 1 } : { c1: c, r1: 0, c2: c, r2: Math.max(usedOf(cur()).rows - 1, 0) }; }
        else if (rh.dataset.r) { const r = +rh.dataset.r; st.sel = e.shiftKey ? { c1: 0, r1: st.sel.r1, c2: cols - 1, r2: r } : { c1: 0, r1: r, c2: Math.max(usedOf(cur()).cols - 1, 0), r2: r }; }
        paintSel();
        if (e.pointerType !== "mouse") { const el = ch || rh; pressTimer = setTimeout(() => headerMenu(el, ch ? "col" : "row"), 550); }
        return;
      }
      const at = cellAt(e.target);
      if (!at) return;
      if (st.editing && st.editing.formula && st.editing.tab === cur().name) { insertRef(at, e.shiftKey); e.preventDefault(); return; }
      commitEdit();
      if (e.shiftKey) { st.sel.c2 = at.c; st.sel.r2 = at.r; } else st.sel = { c1: at.c, r1: at.r, c2: at.c, r2: at.r };
      dragging = e.pointerType === "mouse";
      paintSel();
      grid.focus({ preventScroll: true });
      if (e.pointerType !== "mouse") pressTimer = setTimeout(() => cellMenu(e.target.closest("td") || grid), 550);
    }
    grid.addEventListener("mousedown", (e) => { if (st.editing && st.editing.formula && cellAt(e.target)) e.preventDefault(); });
    grid.addEventListener("pointermove", (e) => {
      if (pressTimer && (Math.abs(e.movementX) + Math.abs(e.movementY) > 6)) { clearTimeout(pressTimer); pressTimer = null; }
      if (!dragging) return;
      const at = cellAt(document.elementFromPoint(e.clientX, e.clientY) || e.target);
      if (at && (at.c !== st.sel.c2 || at.r !== st.sel.r2)) { st.sel.c2 = at.c; st.sel.r2 = at.r; paintSel(); }
    });
    const endPress = () => { dragging = false; if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } };
    grid.addEventListener("pointerup", endPress);
    grid.addEventListener("pointercancel", endPress);
    grid.addEventListener("click", (e) => {                       // a column header's ▾: sort, filter, total …
      const b = e.target.closest(".col-menu");
      if (!b) return;
      e.preventDefault();
      commitEdit();
      const c = +b.dataset.c, r = rangeOf(st.sel);
      if (!(c >= r.c1 && c <= r.c2 && r.r1 === 0)) { st.sel = { c1: c, r1: 0, c2: c, r2: Math.max(usedOf(cur()).rows - 1, 0) }; paintSel(); }
      headerMenu(b.closest("th.ch") || b, "col");
    });
    grid.addEventListener("dblclick", (e) => { const at = cellAt(e.target); if (at && canEdit) startEdit(null, cellInput); });
    grid.addEventListener("contextmenu", (e) => {
      const ch = e.target.closest("th.ch"), rh = e.target.closest("th.rh[data-r]");
      if (ch || rh) {
        e.preventDefault();
        const r = rangeOf(st.sel);
        if (ch && !(+ch.dataset.c >= r.c1 && +ch.dataset.c <= r.c2)) { const c = +ch.dataset.c; st.sel = { c1: c, r1: 0, c2: c, r2: Math.max(usedOf(cur()).rows - 1, 0) }; paintSel(); }
        if (rh && !(+rh.dataset.r >= r.r1 && +rh.dataset.r <= r.r2)) { const rr = +rh.dataset.r; st.sel = { c1: 0, r1: rr, c2: Math.max(usedOf(cur()).cols - 1, 0), r2: rr }; paintSel(); }
        headerMenu(ch || rh, ch ? "col" : "row");
        return;
      }
      const td = e.target.closest("td.cell[data-r]");
      if (td) { e.preventDefault(); const at = cellAt(td); const r = rangeOf(st.sel); if (!(at.c >= r.c1 && at.c <= r.c2 && at.r >= r.r1 && at.r <= r.r2)) { st.sel = { c1: at.c, r1: at.r, c2: at.c, r2: at.r }; paintSel(); } cellMenu(td); }
    });
    function startResize(e, c) {
      e.preventDefault();
      const t = cur();
      const x0 = e.clientX, w0 = widthOf(t, c);
      const move = (ev) => { const w = Math.max(30, Math.min(800, w0 + ev.clientX - x0)); t.cols = Object.assign({}, t.cols, { [S.colName(c)]: w }); draw(); };
      const up = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", up); const w = widthOf(t, c); t.cols[S.colName(c)] = w0; setMeta((x) => { x.cols = Object.assign({}, x.cols, { [S.colName(c)]: w }); }); };
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    }
    function moveSel(dc, dr, extend) {
      const { rows, cols } = lastDraw || dims();
      if (extend) { st.sel.c2 = Math.max(0, Math.min(MAXC - 1, st.sel.c2 + dc)); st.sel.r2 = Math.max(0, Math.min(MAXR - 1, st.sel.r2 + dr)); }
      else {
        const c = Math.max(0, Math.min(MAXC - 1, st.sel.c1 + dc));
        let r = Math.max(0, Math.min(MAXR - 1, st.sel.r1 + dr));
        st.sel = { c1: c, r1: r, c2: c, r2: r };
      }
      const r = Math.max(st.sel.r1, st.sel.r2 ?? st.sel.r1), c = Math.max(st.sel.c1, st.sel.c2 ?? st.sel.c1);
      if (r >= rows - 50 || c >= cols - 3) { draw(); scrollToSel(); draw(); return; }    // near the edge: a bigger grid
      scrollToSel();
      paintSel();
    }
    function scrollToSel() {
      const r = st.sel.r2 ?? st.sel.r1, c = st.sel.c2 ?? st.sel.c1;
      const d = lastDraw;
      const vi = d && d.vis ? Math.max(0, d.vis.indexOf(r)) : r;
      const fr = d ? d.fr : 0;
      const top = (vi) * RH, frozenH = RH + fr * RH;
      if (vi >= fr) {
        if (top < grid.scrollTop + frozenH - RH) grid.scrollTop = Math.max(0, top - frozenH + RH);
        else if (top + RH * 2 > grid.scrollTop + grid.clientHeight) grid.scrollTop = top + RH * 2 - grid.clientHeight + RH;
      }
      if (d) {
        const x = d.lefts[c] || 0, w = widthOf(cur(), c), frozenW = d.lefts[d.fc] || RHEAD;
        if (c >= d.fc) {
          if (x < grid.scrollLeft + frozenW) grid.scrollLeft = Math.max(0, x - frozenW);
          else if (x + w > grid.scrollLeft + grid.clientWidth) grid.scrollLeft = x + w - grid.clientWidth + 4;
        }
      }
    }
    function gotoRef(text) {
      const t = (text || "").trim().toUpperCase();
      const m = /^(?:'?([^'!]+)'?!)?([A-Z]{1,3}\d+(?::[A-Z]{1,3}\d+)?)$/.exec(t);
      if (!m) { toast("Type a cell like C14 or a range like A1:B9.", true); return; }
      if (m[1]) { const i = st.sheet.tabs.findIndex((x) => x.name.toUpperCase() === m[1]); if (i >= 0) { st.tab = i; drawTabs(); } }
      const r = S.parseRange(m[2]);
      if (!r || r.c2 >= MAXC || r.r2 >= MAXR) { toast(`Cells go up to ${S.colName(MAXC - 1)}${MAXR}.`, true); return; }
      st.sel = { c1: r.c1, r1: r.r1, c2: r.c2, r2: r.r2 };
      scrollToSel(); draw(); grid.focus();
    }
    // Find (SPEC §1): what cells show (or hold) on every tab; a result selects the cell
    function findDialog() {
      const input = h("input", { type: "search", id: "sheetFindInput", placeholder: "Text or a number as shown", "aria-label": "Find", maxlength: "200" });
      const list = h("div", { class: "picker-list", id: "sheetFindList" });
      const m = openModal("Find in this sheet", h("div", { class: "find-box" }, input, list), { focus: false });
      const run = debounce(() => {
        const q = input.value.trim().toLowerCase();
        if (!q) { mount(list); return; }
        const hits = [];
        st.sheet.tabs.forEach((t, ti) => {
          if (t.kind !== "grid") return;
          for (const [ref, cell] of Object.entries(t.cells)) {
            const p = S.parseRef(ref);
            if (!p || !cell) continue;
            const shown = S.format(value(t, p.c, p.r), cell, opts).text;
            const raw = typeof cell.v === "string" ? cell.v : "";
            if (shown.toLowerCase().includes(q) || raw.toLowerCase().includes(q)) hits.push({ ti, t, ref, p, shown });
          }
        });
        hits.sort((a, b) => a.ti - b.ti || a.p.r - b.p.r || a.p.c - b.p.c);
        mount(list, hits.length ? hits.slice(0, 200).map((x) => h("button", { class: "picker-row find-hit", type: "button", dataset: { ref: x.ref, tab: x.t.name },
          onclick: () => {
            m.close();
            st.tab = x.ti; st.sel = { c1: x.p.c, r1: x.p.r, c2: x.p.c, r2: x.p.r };
            drawTabs(); scrollToSel(); draw();
            const td = table.querySelector("td.active");
            if (td) { td.classList.add("flash"); setTimeout(() => td.classList.remove("flash"), 2400); }
            grid.focus({ preventScroll: true });
          } }, st.sheet.tabs.length > 1 ? `${x.t.name} › ${x.ref}` : x.ref, h("span", { class: "hint find-shown" }, x.shown)))
          .concat(hits.length > 200 ? [h("div", { class: "hint" }, `…and ${hits.length - 200} more — type more to narrow it down.`)] : [])
          : h("div", { class: "empty" }, "Nothing found."));
      }, 150);
      input.addEventListener("input", run);
      setTimeout(() => input.focus(), 40);
    }
    refBox.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); gotoRef(refBox.value); } if (e.key === "Escape") { refBox.value = rangeText(rangeOf(st.sel)); grid.focus(); } });

    // ------------------------------------------------------------ editing a cell
    function placeInput() {
      if (!st.editing || st.editing.input !== cellInput) { cellInput.hidden = true; return; }
      const td = table.querySelector(`td[data-c="${st.editing.c}"][data-r="${st.editing.r}"]`);
      if (!td) { cellInput.hidden = true; return; }
      cellInput.hidden = false;
      const a = td.getBoundingClientRect(), g = grid.getBoundingClientRect();
      cellInput.style.left = (a.left - g.left + grid.scrollLeft) + "px";
      cellInput.style.top = (a.top - g.top + grid.scrollTop) + "px";
      cellInput.style.width = Math.max(a.width, 120) + "px";
      cellInput.style.height = a.height + "px";
    }
    function startEdit(initial, input) {
      if (roBlocked()) return;
      if (cur().kind !== "grid") return;
      const c = st.sel.c1, r = st.sel.r1;
      st.sel = { c1: c, r1: r, c2: c, r2: r };
      const text = initial !== null && initial !== undefined ? initial : inputText(cur().cells[S.addr(c, r)]);
      st.editing = { c, r, input, tab: cur().name, original: bar.value };
      cellInput.value = text; bar.value = text;
      st.editing.formula = text.startsWith("=");
      if (input === cellInput) { placeInput(); cellInput.focus(); } else bar.focus();
      const end = text.length;
      try { input.setSelectionRange(end, end); } catch (e) { /* ignore */ }
      draw();
    }
    function commitEdit(move) {
      const ed = st.editing;
      if (!ed) return;
      st.editing = null;
      hideSuggest();
      cellInput.hidden = true;
      const text = ed.input.value;
      const t = st.sheet.tabs.find((x) => x.name === ed.tab);
      if (!t) return;
      const ref = S.addr(ed.c, ed.r);
      const old = t.cells[ref] || null;
      if (text === inputText(old)) { draw(); if (move) moveSel(move[0], move[1]); return; }
      const parsed = S.parseInput(text, opts.numStyle);
      let cell = null;
      if (parsed.v !== null) {
        cell = Object.assign({}, old || {}, { v: parsed.v });
        delete cell.q; delete cell.x; delete cell.c; delete cell.t;
        if (parsed.text) cell.q = 1;
        if (parsed.f && (!old || !old.f || old.f === "general")) { cell.f = parsed.f; if (parsed.d !== undefined) cell.d = parsed.d; }
        if (old && old.f === "text" && !parsed.text && typeof parsed.v !== "string") { cell.v = text; if (text.startsWith("=")) cell.q = 1; }
      } else if (old && (old.f || old.b || old.al || old.red)) {
        cell = Object.assign({}, old); delete cell.v; delete cell.q; delete cell.c; delete cell.x;
      }
      if (cell && typeof cell.v === "string" && cell.v.length > 32767) { toast("A cell can hold at most 32 767 characters.", true); return; }
      if (cell && isFormulaCell(cell)) {
        try { S.parse(cell.v.slice(1)); } catch (e) { toast("Formula problem: " + e.message, true); }
      }
      if (cell && !old && countCells() >= MAXCELLS) { toast(`A sheet can have at most ${MAXCELLS.toLocaleString()} filled cells.`, true); return; }
      setCells([{ tab: t.name, ref, cell }]);
      if (move) moveSel(move[0], move[1]); else draw();
    }
    function cancelEdit() { if (!st.editing) return; st.editing = null; hideSuggest(); cellInput.hidden = true; draw(); grid.focus(); }
    function countCells() { let n = 0; for (const t of grids()) n += Object.keys(t.cells).length; return n; }
    function insertRef(at, extend) {
      const inp = st.editing.input;
      const ref = S.addr(at.c, at.r);
      const v = inp.value, pos = inp.selectionStart ?? v.length;
      const before = v.slice(0, pos);
      const m = /(\$?[A-Z]{1,3}\$?\d+)(:\$?[A-Z]{1,3}\$?\d+)?$/.exec(before);
      let nv;
      if (m && extend) nv = before.slice(0, m.index) + m[1] + ":" + ref + v.slice(pos);
      else if (m && /[A-Z0-9]$/.test(before)) nv = before.slice(0, m.index) + ref + v.slice(pos);
      else nv = before + ref + v.slice(pos);
      inp.value = nv; (inp === bar ? cellInput : bar).value = nv;
      const p = nv.length - (v.length - pos);
      inp.focus();
      try { inp.setSelectionRange(p, p); } catch (e) { /* ignore */ }
    }
    // Pointing with the arrow keys: while a formula is being typed, right after "=", an operator, "(" or ",",
    // an arrow key picks the cell next to the one being edited and writes its address in; more arrows move that
    // pick, Shift extends it to a range; typing anything else ends the pointing (as in other spreadsheets).
    const POINT_AFTER = /[=+\-*/^&<>(,;]\s*$/;
    const REF_AT_END = /(\$?[A-Z]{1,3}\$?\d+)(:\$?[A-Z]{1,3}\$?\d+)?$/;
    function pointWithArrow(e, inp) {
      const moves = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
      const mv = moves[e.key];
      if (!mv || !st.editing || !st.editing.formula || st.editing.tab !== cur().name) return false;
      const v = inp.value, pos = inp.selectionStart ?? v.length;
      const before = v.slice(0, pos);
      const pt = st.editing.point;
      const pointing = pt && pt.end === pos && pt.input === inp;
      if (!pointing && !POINT_AFTER.test(before)) return false;
      const from = pointing ? (e.shiftKey ? pt.head : pt) : { c: st.editing.c, r: st.editing.r };
      const to = { c: Math.max(0, Math.min(S.MAXC - 1, from.c + mv[0])), r: Math.max(0, Math.min(S.MAXR - 1, from.r + mv[1])) };
      const anchor = pointing && e.shiftKey ? pt.anchor : to;
      const text = e.shiftKey && pointing ? rangeText(rangeOf({ c1: anchor.c, r1: anchor.r, c2: to.c, r2: to.r })) : S.addr(to.c, to.r);
      const m = pointing ? REF_AT_END.exec(before) : null;
      const start = m ? m.index : pos;
      const nv = v.slice(0, start) + text + v.slice(pos);
      inp.value = nv; (inp === bar ? cellInput : bar).value = nv;
      const end = start + text.length;
      try { inp.setSelectionRange(end, end); } catch (x) { /* ignore */ }
      st.editing.point = { c: to.c, r: to.r, head: to, anchor: e.shiftKey && pointing ? anchor : to, end, input: inp };
      paintPoint(rangeOf({ c1: anchor.c, r1: anchor.r, c2: to.c, r2: to.r }));
      e.preventDefault(); e.stopPropagation();
      return true;
    }
    // the picked cell (or range) gets a dashed outline while it's being pointed at; nothing when `range` is null
    function paintPoint(range) {
      for (const el of table.querySelectorAll("td.point")) el.classList.remove("point");
      if (!range) return;
      let first = null;
      for (const td of table.querySelectorAll("tbody td.cell")) {
        const c = +td.dataset.c, r = +td.dataset.r;
        if (c >= range.c1 && c <= range.c2 && r >= range.r1 && r <= range.r2) { td.classList.add("point"); if (!first || (r === range.r1 && c === range.c1)) first = td; }
      }
      if (first) try { first.scrollIntoView({ block: "nearest", inline: "nearest" }); } catch (x) { /* ignore */ }
    }
    function editKeys(e, inp) {
      if (suggest.hidden && pointWithArrow(e, inp)) return;
      if (["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp"].includes(e.key)) e.stopPropagation();   // the grid (its parent) mustn't move too
      if (!suggest.hidden && ["ArrowDown", "ArrowUp", "Tab", "Enter"].includes(e.key)) {
        const items = [...suggest.querySelectorAll("button")];
        let i = items.findIndex((x) => x.classList.contains("on"));
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); i = (i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; items.forEach((x, k) => x.classList.toggle("on", k === i)); return; }
        if (i >= 0) { e.preventDefault(); items[i].click(); return; }
      }
      if (e.key === "Enter") { e.preventDefault(); commitEdit([0, e.shiftKey ? -1 : 1]); grid.focus(); }
      else if (e.key === "Tab") { e.preventDefault(); commitEdit([e.shiftKey ? -1 : 1, 0]); grid.focus(); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); cancelEdit(); }
    }
    function onType(inp) {
      const other = inp === bar ? cellInput : bar;
      other.value = inp.value;
      if (st.editing) { st.editing.formula = inp.value.startsWith("="); if (st.editing.point) { st.editing.point = null; paintPoint(null); } }
      autocomplete(inp);
    }
    cellInput.addEventListener("keydown", (e) => editKeys(e, cellInput));
    cellInput.addEventListener("input", () => onType(cellInput));
    bar.addEventListener("focus", () => { if (!st.editing && canEdit && cur().kind === "grid") startEdit(null, bar); });
    bar.addEventListener("keydown", (e) => editKeys(e, bar));
    bar.addEventListener("input", () => onType(bar));
    cellInput.addEventListener("blur", () => setTimeout(() => { if (st.editing && st.editing.input === cellInput && document.activeElement !== cellInput && document.activeElement !== bar && !suggest.contains(document.activeElement)) commitEdit(); }, 120));
    bar.addEventListener("blur", () => setTimeout(() => { if (st.editing && st.editing.input === bar && document.activeElement !== bar && !suggest.contains(document.activeElement) && !grid.contains(document.activeElement)) commitEdit(); }, 150));

    // autocomplete: =SU → SUM, SUMIF, SUMIFS
    function autocomplete(inp) {
      const v = inp.value, pos = inp.selectionStart ?? v.length;
      if (!v.startsWith("=")) { hideSuggest(); return; }
      const before = v.slice(0, pos);
      const quotes = (before.match(/"/g) || []).length;
      const m = /(^|[=(,+\-*/^&<> ])([A-Za-z][A-Za-z0-9.]*)$/.exec(before);
      if (!m || quotes % 2) { hideSuggest(); return; }
      const word = m[2].toUpperCase();
      const names = Object.keys(S.FUNCTIONS).filter((n) => n.startsWith(word)).sort();
      if (!names.length || (names.length === 1 && names[0] === word && before.endsWith("("))) { hideSuggest(); return; }
      mount(suggest, names.slice(0, 8).map((n, i) => h("button", { type: "button", role: "option", class: i === 0 ? "on" : "", dataset: { fn: n }, onmousedown: (e) => e.preventDefault(), onclick: () => {
        const start = pos - m[2].length;
        const nv = v.slice(0, start) + n + "(" + v.slice(pos);
        inp.value = nv; onType(inp); hideSuggest();
        const p = start + n.length + 1; inp.focus(); try { inp.setSelectionRange(p, p); } catch (e) { /* ignore */ }
      } }, h("strong", null, n), h("span", { class: "hint" }, " (" + S.FUNCTIONS[n].args + ")"))));
      suggest.hidden = false;
      if (inp === cellInput) {
        const r = cellInput.getBoundingClientRect(), br = bar.parentNode.getBoundingClientRect();
        suggest.style.left = Math.max(0, r.left - br.left) + "px"; suggest.style.top = (r.bottom - br.top + 2) + "px";
      } else { suggest.style.left = "0px"; suggest.style.top = ""; }
    }
    function hideSuggest() { suggest.hidden = true; suggest.replaceChildren(); }

    // ƒ — the functions, with examples; tapping one puts it into the cell
    fBtn.addEventListener("click", () => {
      const q = h("input", { type: "search", placeholder: "Find a function", "aria-label": "Find a function", id: "fxSearch" });
      const list = h("div", { class: "fx-list", id: "fxList" });
      const drawList = () => {
        const w = q.value.trim().toUpperCase();
        mount(list, Object.keys(S.FUNCTIONS).sort().filter((n) => !w || n.includes(w) || S.FUNCTIONS[n].desc.toUpperCase().includes(w)).map((n) => {
          const f = S.FUNCTIONS[n];
          return h("button", { class: "fx-row", type: "button", dataset: { fn: n }, onclick: () => {
            m.close();
            if (!canEdit) return;
            if (!st.editing) startEdit("=", fine() ? cellInput : bar);
            const inp = st.editing.input;
            const v = inp.value.startsWith("=") ? inp.value : "=" + inp.value;
            inp.value = v + n + "("; onType(inp); inp.focus();
          } }, h("span", { class: "fx-name" }, `${n}(${f.args})`), h("span", { class: "fx-desc" }, f.desc), h("code", { class: "fx-ex" }, f.example));
        }));
      };
      q.addEventListener("input", drawList);
      const m = openModal("Functions", h("div", null, q, list, h("p", { class: "hint" }, "Errors: ", Object.keys(S.WHY).map((k) => h("span", { class: "err-key", title: S.WHY[k] }, k + " ")))), { wide: true, focus: false });
      drawList();
      setTimeout(() => q.focus(), 30);
    });

    // ------------------------------------------------------------ formats
    function selCells(fn) {        // fn(cell or null, ref) → new cell — over the selection (filled cells, and the active one)
      const t = cur();
      const r = rangeOf(st.sel);
      const out = [];
      const r2 = Math.min(r.r2, Math.max(usedOf(t).rows - 1, r.r1));
      for (let row = r.r1; row <= r2; row++) for (let c = r.c1; c <= r.c2; c++) {
        const ref = S.addr(c, row);
        const cell = t.cells[ref] || null;
        if (!cell && !(row === r.r1 && c === r.c1) && (r.r2 - r.r1 + 1) * (r.c2 - r.c1 + 1) > 1 && !(r.r2 - r.r1 < 200)) continue;
        out.push({ tab: t.name, ref, cell: fn(cell ? Object.assign({}, cell) : {}, ref) });
      }
      return out;
    }
    function setFormat(f) {
      if (roBlocked() || cur().kind !== "grid") return;
      if (csv && f.f && !["date", "time", "datetime"].includes(f.f)) { csvBlocked(); fmtSel.value = (activeCell() && activeCell().f) || "general"; return; }
      setCells(selCells((cell) => {
        if (f.f) { cell.f = f.f; if (NO_DECIMALS.has(f.f)) delete cell.d; } else delete cell.f;
        if (f.f === "text" && typeof cell.v === "number") { /* keeps the number; shown as typed */ }
        return cell;
      }));
    }
    function decimals(step) {
      if (roBlocked() || csvBlocked()) return;
      setCells(selCells((cell) => {
        const def = cell.f === "percent" ? 0 : 2;
        if (!cell.f || cell.f === "general" || NO_DECIMALS.has(cell.f)) cell.f = "number";
        cell.d = Math.max(0, Math.min(10, (cell.d ?? def) + step));
        return cell;
      }));
    }
    function toggle(key) {
      if (roBlocked() || csvBlocked()) return;
      const a = activeCell();
      const on = !(a && a[key]);
      setCells(selCells((cell) => { if (on) cell[key] = 1; else delete cell[key]; return cell; }));
    }
    function align(a) {
      if (roBlocked() || csvBlocked()) return;
      const cell = activeCell();
      const same = cell && cell.al === a;
      setCells(selCells((x) => { if (same) delete x.al; else x.al = a; return x; }));
    }

    // ------------------------------------------------------------ clipboard, fill, clear
    function selectionTSV(r) {
      const t = cur();
      const rows = [];
      for (let row = r.r1; row <= r.r2; row++) {
        const line = [];
        for (let c = r.c1; c <= r.c2; c++) { const cell = t.cells[S.addr(c, row)]; line.push(cell ? S.format(value(t, c, row), cell, opts).text.replace(/[\t\n\r]/g, " ") : ""); }
        rows.push(line.join("\t"));
      }
      return rows.join("\n");
    }
    function copySel(cut) {
      const t = cur();
      const r = rangeOf(st.sel);
      const r2 = Math.min(r.r2, Math.max(r.r1, usedOf(t).rows - 1));
      const rr = Object.assign({}, r, { r2 });
      const cells = [];
      for (let row = rr.r1; row <= rr.r2; row++) for (let c = rr.c1; c <= rr.c2; c++) { const cell = t.cells[S.addr(c, row)]; if (cell) cells.push([c - rr.c1, row - rr.r1, clone(cell)]); }
      const text = selectionTSV(rr);
      st.clip = { text, cells, r: rr, cut: !!cut, tab: t.name };
      return text;
    }
    document.addEventListener("copy", onCopy);
    document.addEventListener("cut", onCut);
    document.addEventListener("paste", onPaste);
    function gridActive() { return document.activeElement === grid && !st.editing && cur().kind === "grid"; }
    function onCopy(e) { if (!gridActive()) return; e.preventDefault(); e.clipboardData.setData("text/plain", copySel(false)); toast("Copied"); }
    function onCut(e) {
      if (!gridActive() || roBlocked()) return;
      e.preventDefault();
      e.clipboardData.setData("text/plain", copySel(true));
      clearSel();
    }
    function onPaste(e) {
      if (!gridActive() || roBlocked()) return;
      e.preventDefault();
      const text = e.clipboardData.getData("text/plain");
      pasteText(text);
    }
    function pasteText(text) {
      const t = cur();
      const at = rangeOf(st.sel);
      const changes = [];
      if (st.clip && text.replace(/\r/g, "") === st.clip.text) {      // from this sheet: formulas move
        const dc = at.c1 - st.clip.r.c1, dr = at.r1 - st.clip.r.r1;
        for (let row = 0; row <= st.clip.r.r2 - st.clip.r.r1; row++) for (let c = 0; c <= st.clip.r.c2 - st.clip.r.c1; c++) {
          if (at.c1 + c >= MAXC || at.r1 + row >= MAXR) continue;
          changes.push({ tab: t.name, ref: S.addr(at.c1 + c, at.r1 + row), cell: null });
        }
        const byRef = new Map(changes.map((x) => [x.ref, x]));
        for (const [c, row, cell] of st.clip.cells) {
          const ref = S.addr(at.c1 + c, at.r1 + row);
          if (!byRef.has(ref)) continue;
          const nc = clone(cell);
          if (isFormulaCell(nc)) nc.v = S.shiftFormula(nc.v, dc, dr);
          byRef.get(ref).cell = nc;
        }
      } else {
        const lines = text.replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n");
        if (lines.length > MAXR) { toast("That's more rows than a sheet holds.", true); return; }
        lines.forEach((line, i) => line.split("\t").forEach((val, j) => {
          if (at.c1 + j >= MAXC || at.r1 + i >= MAXR) return;
          const ref = S.addr(at.c1 + j, at.r1 + i);
          const parsed = S.parseInput(val, opts.numStyle);
          const old = t.cells[ref];
          const cell = parsed.v === null ? null : Object.assign({}, old && { f: old.f, d: old.d, b: old.b, al: old.al, red: old.red }, { v: parsed.v }, parsed.text ? { q: 1 } : {}, parsed.f && !(old && old.f) ? { f: parsed.f, d: parsed.d } : {});
          changes.push({ tab: t.name, ref, cell });
        }));
        const rows = lines.length, cols = Math.max(...lines.map((l) => l.split("\t").length));
        st.sel = { c1: at.c1, r1: at.r1, c2: Math.min(MAXC - 1, at.c1 + cols - 1), r2: Math.min(MAXR - 1, at.r1 + rows - 1) };
      }
      if (st.clip && st.clip.cut && st.clip.tab === t.name && text.replace(/\r/g, "") === st.clip.text) st.clip = null;
      setCells(changes);
    }
    function clearSel() {
      if (roBlocked()) return;
      const t = cur();
      const r = rangeOf(st.sel);
      const changes = [];
      for (const ref of Object.keys(t.cells)) {
        const p = S.parseRef(ref);
        if (p && p.c >= r.c1 && p.c <= r.c2 && p.r >= r.r1 && p.r <= r.r2) {
          const cell = Object.assign({}, t.cells[ref]);
          delete cell.v; delete cell.q; delete cell.c; delete cell.x;
          changes.push({ tab: t.name, ref, cell: Object.keys(cell).length ? cell : null });
        }
      }
      setCells(changes);
    }
    /** Ctrl+D / Ctrl+R: the first row (column) of the selection into the rest; $ keeps a reference fixed. */
    function fill(dir) {
      if (roBlocked()) return;
      const t = cur();
      const r = rangeOf(st.sel);
      const changes = [];
      if (dir === "down") {
        if (r.r2 === r.r1) { if (r.r1 === 0) return; r.r1 -= 1; }
        for (let c = r.c1; c <= r.c2; c++) {
          const src = t.cells[S.addr(c, r.r1)];
          for (let row = r.r1 + 1; row <= r.r2; row++) {
            const nc = src ? clone(src) : null;
            if (nc && isFormulaCell(nc)) nc.v = S.shiftFormula(nc.v, 0, row - r.r1);
            changes.push({ tab: t.name, ref: S.addr(c, row), cell: nc });
          }
        }
      } else {
        if (r.c2 === r.c1) { if (r.c1 === 0) return; r.c1 -= 1; }
        for (let row = r.r1; row <= r.r2; row++) {
          const src = t.cells[S.addr(r.c1, row)];
          for (let c = r.c1 + 1; c <= r.c2; c++) {
            const nc = src ? clone(src) : null;
            if (nc && isFormulaCell(nc)) nc.v = S.shiftFormula(nc.v, c - r.c1, 0);
            changes.push({ tab: t.name, ref: S.addr(c, row), cell: nc });
          }
        }
      }
      setCells(changes);
    }

    // ------------------------------------------------------------ rows and columns (references adjust)
    function shiftRangeText(text, axis, at, count, tabName, home) {
      const out = S.adjustFormula("=" + text, { tab: tabName, home, axis, at, count });
      return out.includes("#REF!") ? null : out.slice(1).replace(/\$/g, "");
    }
    function insertDelete(axis, at, count) {
      if (roBlocked()) return;
      const t = cur();
      const used = usedOf(t);
      if (count > 0 && axis === "row" && used.rows + count > MAXR) { toast(`A tab can have at most ${MAXR.toLocaleString()} rows.`, true); return; }
      if (count > 0 && axis === "col" && used.cols + count > MAXC) { toast(`A tab can have at most ${MAXC} columns.`, true); return; }
      setShape(() => {
        const moved = {};
        for (const [ref, cell] of Object.entries(t.cells)) {
          const p = S.parseRef(ref);
          let { c, r } = p;
          const k = axis === "row" ? r : c;
          if (count < 0 && k >= at && k < at - count) continue;
          if (k >= at) { if (axis === "row") r += count; else c += count; }
          if (r < 0 || c < 0 || r >= MAXR || c >= MAXC) continue;
          moved[S.addr(c, r)] = cell;
        }
        t.cells = moved;
        for (const g of grids()) for (const cell of Object.values(g.cells)) {
          if (isFormulaCell(cell)) cell.v = S.adjustFormula(cell.v, { tab: t.name, home: g.name, axis, at, count });
        }
        if (axis === "col") {
          const cols = {};
          for (const [l, w] of Object.entries(t.cols || {})) { const c = S.colIndex(l); if (count < 0 && c >= at && c < at - count) continue; const nc = c >= at ? c + count : c; if (nc >= 0 && nc < MAXC) cols[S.colName(nc)] = w; }
          t.cols = cols;
          if (t.totals) { const tot = {}; for (const [l, fn] of Object.entries(t.totals)) { const c = S.colIndex(l); if (count < 0 && c >= at && c < at - count) continue; const nc = c >= at ? c + count : c; if (nc >= 0 && nc < MAXC) tot[S.colName(nc)] = fn; } t.totals = Object.keys(tot).length ? tot : null; }
        }
        t.cond = (t.cond || []).map((rule) => { const r = shiftRangeText(rule.range, axis, at, count, t.name, t.name); return r ? Object.assign({}, rule, { range: r }) : null; }).filter(Boolean);
        t.charts = (t.charts || []).map((ch) => { const r = shiftRangeText(ch.range, axis, at, count, t.name, t.name); return r ? Object.assign({}, ch, { range: r }) : null; }).filter(Boolean);
        for (const g of st.sheet.tabs) if (g.kind === "chart" && g.chart.source.tab.toLowerCase() === t.name.toLowerCase()) {
          const r = shiftRangeText(g.chart.source.range, axis, at, count, t.name, t.name); if (r) g.chart.source.range = r;
        }
      });
    }
    function sortRows(col, asc, range) {
      if (roBlocked()) return;
      const t = cur();
      const used = usedOf(t);
      let r = range;
      if (!r || (r.r1 === r.r2 && r.c1 === r.c2) || r.r2 - r.r1 >= used.rows) {
        const header = (() => { for (let c = 0; c < used.cols; c++) { const v = value(t, c, 0); if (v !== null && typeof v !== "string") return false; } return used.rows > 1; })();
        r = { c1: 0, r1: header ? 1 : 0, c2: Math.max(0, used.cols - 1), r2: Math.max(0, used.rows - 1) };
      }
      if (r.r2 <= r.r1) return;
      setShape(() => {
        const rows = [];
        for (let row = r.r1; row <= r.r2; row++) rows.push({ row, key: value(t, col, row) });
        rows.sort((a, b) => {
          const ea = a.key === null || a.key === "", eb = b.key === null || b.key === "";
          if (ea || eb) return ea === eb ? a.row - b.row : (ea ? 1 : -1);         // empty cells last, either way
          const x = S.compare(S.isErr(a.key) ? "" : a.key, S.isErr(b.key) ? "" : b.key);
          return (asc ? x : -x) || a.row - b.row;
        });
        const old = {};
        for (let row = r.r1; row <= r.r2; row++) for (let c = r.c1; c <= r.c2; c++) { const ref = S.addr(c, row); if (t.cells[ref]) { old[ref] = t.cells[ref]; delete t.cells[ref]; } }
        rows.forEach((x, i) => {
          const to = r.r1 + i;
          for (let c = r.c1; c <= r.c2; c++) {
            const cell = old[S.addr(c, x.row)];
            if (!cell) continue;
            const nc = clone(cell);
            if (isFormulaCell(nc) && to !== x.row) nc.v = S.shiftFormula(nc.v, 0, to - x.row, { sameTabOnly: true });
            t.cells[S.addr(c, to)] = nc;
          }
        });
      }, `Sorted ${rangeText(r)} by column ${S.colName(col)}`);
    }
    function headerMenu(anchor, axis) {
      const r = rangeOf(st.sel);
      const t = cur();
      if (axis === "col") {
        const n = r.c2 - r.c1 + 1;
        D.menu(anchor, [
          canEdit && { id: "col-left", label: "Insert column left", run: () => insertDelete("col", r.c1, 1) },
          canEdit && { id: "col-right", label: "Insert column right", run: () => insertDelete("col", r.c2 + 1, 1) },
          canEdit && { id: "col-delete", label: n > 1 ? `Delete ${n} columns` : "Delete column", danger: true, run: () => insertDelete("col", r.c1, -n) },
          canEdit && "-",
          canEdit && { id: "col-sort-az", label: "Sort A → Z", run: () => sortRows(r.c1, true, null) },
          canEdit && { id: "col-sort-za", label: "Sort Z → A", run: () => sortRows(r.c1, false, null) },
          { id: "col-filter", label: st.filters[t.name] && st.filters[t.name].col === r.c1 ? "Change filter…" : "Filter…", run: () => filterDialog(r.c1) },
          canEdit && !csv && { id: "col-width", label: "Width…", run: () => widthDialog(r.c1, r.c2) },
          canEdit && !csv && { id: "col-freeze", label: `Freeze columns A–${S.colName(r.c2)}`, run: () => setMeta((x) => { x.freeze = Object.assign({ r: 0 }, x.freeze, { c: Math.min(20, r.c2 + 1) }); }) },
          canEdit && !csv && { id: "col-total", label: "Total…", run: () => totalMenu(anchor, r.c1) },
        ]);
      } else {
        const n = r.r2 - r.r1 + 1;
        D.menu(anchor, [
          canEdit && { id: "row-above", label: "Insert row above", run: () => insertDelete("row", r.r1, 1) },
          canEdit && { id: "row-below", label: "Insert row below", run: () => insertDelete("row", r.r2 + 1, 1) },
          canEdit && { id: "row-delete", label: n > 1 ? `Delete ${n} rows` : "Delete row", danger: true, run: () => insertDelete("row", r.r1, -n) },
          canEdit && !csv && "-",
          canEdit && !csv && { id: "row-freeze", label: `Freeze rows 1–${r.r2 + 1}`, run: () => setMeta((x) => { x.freeze = Object.assign({ c: 0 }, x.freeze, { r: Math.min(50, r.r2 + 1) }); }) },
        ]);
      }
    }
    function cellMenu(anchor) {
      const items = [
        { id: "cell-copy", label: "Copy", run: () => { const text = copySel(false); if (navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast("Copied"), () => {}); } },
        canEdit && { id: "cell-paste", label: "Paste", run: async () => { try { pasteText(await navigator.clipboard.readText()); } catch (e) { toast("Use Ctrl+V (or your phone's Paste) to paste.", true); } } },
        canEdit && { id: "cell-clear", label: "Clear", run: clearSel },
        canEdit && "-",
        canEdit && { id: "cell-fill-down", label: "Fill down", run: () => fill("down") },
        canEdit && { id: "cell-fill-right", label: "Fill right", run: () => fill("right") },
        canEdit && "-",
        canEdit && { id: "cell-row-above", label: "Insert row above", run: () => insertDelete("row", rangeOf(st.sel).r1, 1) },
        canEdit && { id: "cell-col-left", label: "Insert column left", run: () => insertDelete("col", rangeOf(st.sel).c1, 1) },
        canEdit && { id: "cell-row-delete", label: "Delete row", danger: true, run: () => { const r = rangeOf(st.sel); insertDelete("row", r.r1, -(r.r2 - r.r1 + 1)); } },
        canEdit && { id: "cell-col-delete", label: "Delete column", danger: true, run: () => { const r = rangeOf(st.sel); insertDelete("col", r.c1, -(r.c2 - r.c1 + 1)); } },
      ];
      D.menu(anchor, items);
    }
    function freezeMenu(anchor) {
      if (csvBlocked()) return;
      const r = rangeOf(st.sel);
      D.menu(anchor, [
        { id: "freeze-row", label: "Freeze the top row", run: () => setMeta((x) => { x.freeze = { r: 1, c: (x.freeze && x.freeze.c) || 0 }; }) },
        { id: "freeze-rows", label: `Freeze rows 1–${r.r1 + 1}`, run: () => setMeta((x) => { x.freeze = { r: Math.min(50, r.r1 + 1), c: (x.freeze && x.freeze.c) || 0 }; }) },
        { id: "freeze-col", label: "Freeze the first column", run: () => setMeta((x) => { x.freeze = { r: (x.freeze && x.freeze.r) || 0, c: 1 }; }) },
        { id: "freeze-cols", label: `Freeze columns A–${S.colName(r.c1)}`, run: () => setMeta((x) => { x.freeze = { r: (x.freeze && x.freeze.r) || 0, c: Math.min(20, r.c1 + 1) }; }) },
        "-",
        { id: "freeze-none", label: "Unfreeze", run: () => setMeta((x) => { x.freeze = { r: 0, c: 0 }; }) },
      ]);
    }
    function sortMenu(anchor) {
      const r = rangeOf(st.sel);
      const multi = r.r2 > r.r1;
      D.menu(anchor, [
        { id: "sort-az", label: multi ? `Sort ${rangeText(r)} A → Z by column ${S.colName(r.c1)}` : `Sort by column ${S.colName(r.c1)}, A → Z`, run: () => sortRows(r.c1, true, multi ? r : null) },
        { id: "sort-za", label: multi ? `Sort ${rangeText(r)} Z → A by column ${S.colName(r.c1)}` : `Sort by column ${S.colName(r.c1)}, Z → A`, run: () => sortRows(r.c1, false, multi ? r : null) },
      ]);
    }
    function widthDialog(c1, c2) {
      const t = cur();
      const input = h("input", { type: "number", min: "30", max: "800", value: String(widthOf(t, c1)), "aria-label": "Width in pixels", id: "colWidth" });
      const m = openModal(`Width of ${S.colName(c1)}${c2 > c1 ? "–" + S.colName(c2) : ""}`, h("div", null, h("label", { class: "field" }, "Width (pixels)", input),
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", onclick: () => {
            const w = Math.max(30, Math.min(800, parseInt(input.value, 10) || DEFAULT_W));
            m.close();
            setMeta((x) => { const cols = Object.assign({}, x.cols); for (let c = c1; c <= c2; c++) cols[S.colName(c)] = w; x.cols = cols; });
          } }, "Set"))));
    }
    function filterDialog(col) {
      const t = cur();
      const used = usedOf(t);
      const counts = new Map();
      for (let r = 1; r < used.rows; r++) { const shown = S.format(value(t, col, r), t.cells[S.addr(col, r)], opts).text; counts.set(shown, (counts.get(shown) || 0) + 1); }
      const cur0 = st.filters[t.name] && st.filters[t.name].col === col ? st.filters[t.name].allowed : null;
      const vals = [...counts.keys()].sort((a, b) => S.compare(a, b)).slice(0, 500);
      const boxes = vals.map((v) => h("label", { class: "filter-row" }, h("input", { type: "checkbox", checked: !cur0 || cur0.has(v), dataset: { v } }), v === "" ? h("em", null, "(empty)") : v, h("span", { class: "hint" }, ` ${counts.get(v)}`)));
      const all = h("input", { type: "checkbox", checked: !cur0, onchange: (e) => boxes.forEach((b) => { b.querySelector("input").checked = e.target.checked; }) });
      const m = openModal(`Filter column ${S.colName(col)}`, h("div", null,
        h("p", { class: "hint" }, "Rows below the first are shown only when this column holds a ticked value. The filter is for this page only — it isn't saved."),
        h("label", { class: "filter-row" }, all, h("strong", null, "All")), h("div", { class: "filter-list" }, boxes),
        h("div", { class: "actions" },
          h("button", { class: "btn-ghost", type: "button", id: "filterClear", onclick: () => { delete st.filters[t.name]; m.close(); grid.scrollTop = 0; draw(); } }, "Show all rows"),
          h("button", { class: "btn-primary", type: "button", id: "filterApply", onclick: () => {
            const allowed = new Set(boxes.filter((b) => b.querySelector("input").checked).map((b) => b.querySelector("input").dataset.v));
            if (allowed.size === vals.length) delete st.filters[t.name]; else st.filters[t.name] = { col, allowed };
            m.close(); grid.scrollTop = 0; draw();
          } }, "Filter"))), { focus: false });
    }

    // ------------------------------------------------------------ charts and conditional colours
    function chartDialog(existing, chartTab) {
      if (csvBlocked() || roBlocked()) return;
      const t = cur();
      const r = rangeOf(st.sel);
      const used = usedOf(t.kind === "grid" ? t : grids()[0]);
      const def = existing || (chartTab && chartTab.chart) || {};
      const defRange = existing ? existing.range : (chartTab ? chartTab.chart.source.range : ((r.c1 !== r.c2 || r.r1 !== r.r2) ? rangeText(r) : rangeText({ c1: 0, r1: 0, c2: Math.max(0, Math.min(used.cols - 1, 5)), r2: Math.max(0, used.rows - 1) })));
      const type = h("select", { id: "chartType", "aria-label": "Chart type" }, [["bar", "Bar"], ["line", "Line"], ["pie", "Pie"]].map(([v, l]) => h("option", { value: v, selected: (def.type || "bar") === v }, l)));
      const range = h("input", { type: "text", id: "chartRange", value: defRange, "aria-label": "Range", maxlength: "20" });
      const title = h("input", { type: "text", id: "chartTitle", value: def.title || "", "aria-label": "Title", maxlength: "100" });
      const place = h("select", { id: "chartPlace", "aria-label": "Where", disabled: !!existing || !!chartTab }, h("option", { value: "below" }, "Below the grid"), h("option", { value: "tab", selected: !!chartTab }, "On its own tab"));
      const err = h("div", { class: "error-text", role: "alert" });
      const preview = h("div", { class: "chart-preview" });
      const srcTab = chartTab ? grids().find((g) => g.name.toLowerCase() === chartTab.chart.source.tab.toLowerCase()) || grids()[0] : (t.kind === "grid" ? t : grids()[0]);
      const redraw = () => { const rr = S.parseRange(range.value.toUpperCase()); mount(preview, rr ? drawChart({ type: type.value, title: title.value }, chartData(srcTab, value, range.value.toUpperCase()), { w: 480, h: 220 }) : null); };
      [type, range, title].forEach((x) => x.addEventListener("input", redraw));
      const m = openModal(existing || chartTab ? "Change chart" : "Add a chart", h("div", null,
        h("div", { class: "form-grid" }, h("label", { class: "field" }, "Type", type), h("label", { class: "field" }, `Range (on ${srcTab.name})`, range),
          h("label", { class: "field" }, "Title", title), h("label", { class: "field" }, "Where", place)),
        h("p", { class: "hint" }, "The first row names the series and the first column holds the labels (as in Excel). A pie uses the first series."),
        preview, err,
        h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Cancel"),
          h("button", { class: "btn-primary", type: "button", id: "chartSave", onclick: () => {
            const rr = S.parseRange(range.value.toUpperCase());
            if (!rr || rr.c2 >= MAXC || rr.r2 >= MAXR) { err.textContent = "Type a range like A1:C6."; return; }
            const spec = { type: type.value, title: title.value.trim() };
            m.close();
            if (chartTab) { setShape(() => { chartTab.chart = Object.assign({}, chartTab.chart, spec, { source: { tab: chartTab.chart.source.tab, range: rangeText(rr) } }); }); return; }
            if (place.value === "tab") {
              if (st.sheet.tabs.length >= MAXTABS) { toast(`A sheet can have at most ${MAXTABS} tabs.`, true); return; }
              setShape(() => { st.sheet.tabs.push({ name: uniqueTabName("Chart"), kind: "chart", chart: Object.assign(spec, { source: { tab: srcTab.name, range: rangeText(rr) } }) }); st.tab = st.sheet.tabs.length - 1; });
              return;
            }
            setMeta((x) => {
              const list = (x.charts || []).slice();
              if (existing) { const i = list.findIndex((y) => y.id === existing.id); list[i] = Object.assign({}, existing, spec, { range: rangeText(rr) }); }
              else { if (list.length >= 10) { toast("At most 10 charts per tab.", true); return; } list.push(Object.assign({ id: "c" + Date.now().toString(36) }, spec, { range: rangeText(rr) })); }
              x.charts = list;
            });
            setTimeout(() => below.scrollIntoView({ block: "nearest", behavior: "smooth" }), 80);
          } }, existing || chartTab ? "Save" : "Add chart"))), { wide: true, focus: false });
      redraw();
    }
    function condDialog() {
      if (csvBlocked() || roBlocked()) return;
      const t = cur();
      if (t.kind !== "grid") return;
      const body = h("div");
      const m = openModal("Conditional colours", body, { wide: true, focus: false });
      const describe = (rule) => {
        const what = { gt: `greater than ${S.numText(rule.a)}`, lt: `less than ${S.numText(rule.a)}`, between: `between ${S.numText(rule.a)} and ${S.numText(rule.b)}`, contains: `text contains “${rule.a}”`,
          before: `date before ${S.format(rule.a, { f: "date" }, opts).text}`, after: `date after ${S.format(rule.a, { f: "date" }, opts).text}`, top: `top ${rule.n}`, bottom: `bottom ${rule.n}`, dup: "duplicates" }[rule.type];
        return `${rule.range}: ${what} → ${[rule.fill && rule.fill + " fill", rule.text && rule.text + " text"].filter(Boolean).join(", ")}`;
      };
      function drawBody() {
        const r = rangeOf(st.sel);
        const range = h("input", { type: "text", id: "condRange", value: rangeText(r.r1 === r.r2 && r.c1 === r.c2 ? { c1: r.c1, r1: 1, c2: r.c1, r2: Math.max(1, usedOf(t).rows - 1) } : r), maxlength: "20", "aria-label": "Range" });
        const type = h("select", { id: "condType", "aria-label": "Rule" }, RULES.map(([v, l]) => h("option", { value: v }, l)));
        const a = h("input", { type: "text", id: "condA", "aria-label": "Value", placeholder: "Value" });
        const b = h("input", { type: "text", id: "condB", "aria-label": "Second value", placeholder: "and", hidden: true });
        const fill = h("select", { id: "condFill", "aria-label": "Fill colour" }, h("option", { value: "" }, "No fill"), COLOURS.map(([v, l]) => h("option", { value: v, selected: v === "red" }, l + " fill")));
        const text = h("select", { id: "condText", "aria-label": "Text colour" }, h("option", { value: "" }, "Text as it is"), COLOURS.map(([v, l]) => h("option", { value: v }, l + " text")));
        const err = h("div", { class: "error-text", role: "alert" });
        const sync = () => {
          const ty = type.value;
          a.hidden = ty === "dup"; b.hidden = ty !== "between";
          a.type = ty === "before" || ty === "after" ? "date" : "text";
          a.placeholder = ty === "top" || ty === "bottom" ? "How many" : ty === "contains" ? "Text" : "Number";
        };
        type.addEventListener("change", sync); sync();
        mount(body,
          (t.cond || []).length ? h("div", { class: "item-list" }, t.cond.map((rule, i) => h("div", { class: "item-row cond-row" },
            h("span", { class: "cond-swatch " + (rule.fill ? "cf-fill-" + rule.fill : "") + (rule.text ? " cf-text-" + rule.text : "") }, "Ab"),
            h("div", { class: "row-main static" }, h("span", { class: "row-text" }, describe(rule))),
            h("button", { class: "icon-btn danger", type: "button", "aria-label": "Remove rule", title: "Remove", onclick: () => { setMeta((x) => { x.cond = x.cond.filter((_, k) => k !== i); }); drawBody(); } }, "✕")))) : h("p", { class: "hint" }, "No colour rules on this tab yet."),
          h("h4", null, "Add a rule"),
          h("div", { class: "form-grid" }, h("label", { class: "field" }, "Cells", range), h("label", { class: "field" }, "When", type), h("label", { class: "field" }, "Value", h("div", { class: "row-gap" }, a, b)),
            h("label", { class: "field" }, "Fill", fill), h("label", { class: "field" }, "Text", text)),
          err,
          h("div", { class: "actions" }, h("button", { class: "btn-ghost", type: "button", onclick: () => m.close() }, "Done"),
            h("button", { class: "btn-primary", type: "button", id: "condAdd", onclick: () => {
              const rr = S.parseRange(range.value.toUpperCase());
              if (!rr) { err.textContent = "Type cells like B2:B20."; return; }
              const rule = { range: rangeText(rr), type: type.value, fill: fill.value || null, text: text.value || null };
              if (!rule.fill && !rule.text) { err.textContent = "Choose a fill or a text colour."; return; }
              const num = (x) => { const p = S.parseInput(x, opts.numStyle); return typeof p.v === "number" ? p.v : null; };
              if (["gt", "lt", "between"].includes(rule.type)) { rule.a = num(a.value); if (rule.a === null) { err.textContent = "Type a number."; return; } }
              if (rule.type === "between") { rule.b = num(b.value); if (rule.b === null) { err.textContent = "Type the second number."; return; } }
              if (rule.type === "before" || rule.type === "after") { rule.a = num(a.value); if (rule.a === null) { err.textContent = "Choose a date."; return; } }
              if (rule.type === "contains") { if (!a.value.trim()) { err.textContent = "Type the text."; return; } rule.a = a.value.trim(); }
              if (rule.type === "top" || rule.type === "bottom") { rule.n = parseInt(a.value, 10); if (!(rule.n >= 1 && rule.n <= 1000)) { err.textContent = "Type how many (1–1000)."; return; } }
              if ((t.cond || []).length >= 50) { err.textContent = "At most 50 rules per tab."; return; }
              setMeta((x) => { x.cond = (x.cond || []).concat([rule]); });
              drawBody();
            } }, "Add rule")));
      }
      drawBody();
    }

    // ------------------------------------------------------------ export and print
    function exportMenu(anchor) {
      const t = cur();
      const tabQ = t.kind === "grid" ? `&tab=${encodeURIComponent(t.name)}` : "";
      D.menu(anchor, [
        csv ? { id: "export-file", label: "Download .csv", run: () => D.download(doc) } : { id: "export-xlsx", label: "Download .xlsx", run: async () => { await flush(); D.saveUrl(`api/docs/${encodeURIComponent(doc.id)}/export?format=xlsx`); } },
        csv ? { id: "export-xlsx", label: "Download as .xlsx", run: async () => { await flush(); D.saveUrl(`api/docs/${encodeURIComponent(doc.id)}/export?format=xlsx`); } } : null,
        t.kind === "grid" ? { id: "export-csv", label: `CSV of “${t.name}” — values`, run: async () => { await flush(); D.saveUrl(`api/docs/${encodeURIComponent(doc.id)}/export?format=csv&mode=values${tabQ}`); } } : null,
        t.kind === "grid" ? { id: "export-csv-f", label: `CSV of “${t.name}” — formulas`, run: async () => { await flush(); D.saveUrl(`api/docs/${encodeURIComponent(doc.id)}/export?format=csv&mode=formulas${tabQ}`); } } : null,
        "-",
        { id: "export-print", label: "Print or save as PDF", run: printSheet },
      ]);
    }
    function printSheet() {
      const t = cur();
      const old = $("#printArea");
      if (old) old.remove();
      const area = h("div", { id: "printArea", class: "print-area" });
      const headLine = h("div", { class: "print-head" }, h("h1", null, titleOf(doc) + (st.sheet.tabs.length > 1 ? ` › ${t.name}` : "")),
        h("div", { class: "print-meta" }, [doc.ownerName, new Date().toLocaleDateString()].filter(Boolean).join(" · ")));
      area.appendChild(headLine);
      if (t.kind === "chart") area.appendChild(chartPane.querySelector("svg") ? chartPane.querySelector("svg").cloneNode(true) : h("div"));
      else {
        const used = usedOf(t);
        const fr = Math.max(1, (t.freeze && t.freeze.r) || 1);
        const tdOf = (c, r) => {
          const cell = t.cells[S.addr(c, r)];
          if (!cell) return h("td");
          const v = value(t, c, r);
          const s = S.format(v, cell, opts);
          const al = cell.al || (typeof v === "number" ? "right" : null);
          return h("td", { class: [cell.b ? "b" : "", al ? "al-" + al : "", s.negative ? "neg" : ""].filter(Boolean).join(" ") || null }, s.text);
        };
        const rowsOf = (from, to) => { const out = []; for (let r = from; r < to; r++) out.push(h("tr", null, h("th", null, String(r + 1)), Array.from({ length: used.cols }, (_, c) => tdOf(c, r)))); return out; };
        const thead = h("thead", null, h("tr", null, h("th"), Array.from({ length: used.cols }, (_, c) => h("th", null, S.colName(c)))), rowsOf(0, Math.min(fr, used.rows)));
        const foot = t.totals && Object.keys(t.totals).length ? h("tfoot", null, h("tr", null, h("th", null, "Σ"), Array.from({ length: used.cols }, (_, c) => {
          const fn = t.totals[S.colName(c)];
          return h("td", { class: "b al-right" }, fn ? `${fn} ${S.format(totalOf(t, c, fn), fn === "COUNT" ? null : columnFormat(t, c), opts).text}` : "");
        }))) : null;
        area.appendChild(h("table", { class: "print-table" }, thead, h("tbody", null, rowsOf(Math.min(fr, used.rows), used.rows)), foot));
        for (const ch of t.charts || []) area.appendChild(drawChart(ch, chartData(t, value, ch.range)));
        if (used.cols > 7) toast("A wide sheet: choose Landscape in the print dialog.", false, { ms: 6000 });
      }
      document.body.appendChild(area);
      document.documentElement.classList.add("printing");
      const done = () => { document.documentElement.classList.remove("printing"); area.remove(); window.removeEventListener("afterprint", done); };
      window.addEventListener("afterprint", done);
      setTimeout(() => window.print(), 50);
    }

    // ------------------------------------------------------------ keys
    grid.addEventListener("keydown", (e) => {
      if (st.editing) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const k = e.key;
      if (ctrl && (k === "z" || k === "Z")) { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (ctrl && (k === "y" || k === "Y")) { e.preventDefault(); redo(); return; }
      if (ctrl && (k === "d" || k === "D")) { e.preventDefault(); fill("down"); return; }
      if (ctrl && (k === "r" || k === "R")) { e.preventDefault(); fill("right"); return; }
      if (ctrl && (k === "b" || k === "B")) { e.preventDefault(); toggle("b"); return; }
      if (ctrl && (k === "f" || k === "F")) { e.preventDefault(); findDialog(); return; }
      if (ctrl && (k === "a" || k === "A")) { e.preventDefault(); const u = usedOf(cur()); st.sel = { c1: 0, r1: 0, c2: Math.max(0, u.cols - 1), r2: Math.max(0, u.rows - 1) }; draw(); return; }
      const moves = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0], PageDown: [0, 20], PageUp: [0, -20] };
      if (moves[k]) { e.preventDefault(); moveSel(moves[k][0], moves[k][1], e.shiftKey); return; }
      if (k === "Tab") { e.preventDefault(); moveSel(e.shiftKey ? -1 : 1, 0); return; }
      if (k === "Enter") { e.preventDefault(); if (canEdit) startEdit(null, cellInput); return; }
      if (k === "F2") { e.preventDefault(); if (canEdit) startEdit(null, cellInput); return; }
      if (k === "Delete" || k === "Backspace") { e.preventDefault(); clearSel(); return; }
      if (k === "Home") { e.preventDefault(); st.sel = { c1: 0, r1: st.sel.r1, c2: 0, r2: st.sel.r1 }; scrollToSel(); draw(); return; }
      if (!ctrl && !e.altKey && k.length === 1 && canEdit) { e.preventDefault(); startEdit(k, cellInput); }
    });

    // ------------------------------------------------------------ start, polling, leaving
    drawTabs();
    if (ctx.atCell) {
      const i = st.sheet.tabs.findIndex((t) => t.name.toLowerCase() === String(ctx.atCell.tab).toLowerCase());
      if (i >= 0) st.tab = i;
      const p = S.parseRef(String(ctx.atCell.ref || "").toUpperCase());
      if (p) st.sel = { c1: p.c, r1: p.r, c2: p.c, r2: p.r };
      drawTabs();
    }
    requestAnimationFrame(() => {
      draw();
      if (ctx.atCell) {
        scrollToSel(); draw();
        const td = table.querySelector("td.active");
        if (td) { td.classList.add("flash"); setTimeout(() => td.classList.remove("flash"), 2400); }
      }
      grid.focus({ preventScroll: true });
    });
    setStatus(canEdit ? "Saved" : "");
    checkSecret();
    const onResize = debounce(() => { if (document.body.contains(grid)) draw(); }, 150);
    window.addEventListener("resize", onResize);
    const onHide = () => { if (document.visibilityState === "hidden" && hasWork()) save({ keepalive: true }); };
    document.addEventListener("visibilitychange", onHide);
    const cleanup = () => {
      document.removeEventListener("copy", onCopy); document.removeEventListener("cut", onCut); document.removeEventListener("paste", onPaste);
      document.removeEventListener("visibilitychange", onHide); window.removeEventListener("resize", onResize);
      page.classList.remove("sheet-page");
      clearInterval(st.poller);
    };
    D.state.leaving = async () => { commitEdit(); cleanup(); await flush(); };
    let lastDay = todayIn(tz);
    st.poller = setInterval(async () => {
      if (!ctx.current() || D.state.route !== "doc" || !document.body.contains(grid)) { cleanup(); return; }
      if (document.visibilityState !== "visible") return;
      const day = todayIn(tz);
      if (day !== lastDay) { lastDay = day; wb.refreshToday(); draw(); }
      try {
        const e = await api(`api/docs/${doc.id}/etag`);
        if (head && head.editing) { head.editing.hidden = !e.editing; head.editing.textContent = e.editing ? `${e.editing} is editing` : ""; }
        if (e.etag !== st.etag && !hasWork() && !st.saving && !st.editing) {
          const fresh = await api(`api/docs/${doc.id}`);
          if (hasWork() || st.saving || st.editing) return;
          if (fresh.readOnly !== doc.readOnly) { D.render(); return; }
          st.etag = fresh.etag;
          adopt(fresh.sheet, e.updatedByName ? `Updated — ${e.updatedByName} changed it` : "Updated — it was changed outside the app");
        }
      } catch (x) { if (x.status === 404) { cleanup(); toast("This sheet isn't there any more.", true); } }
    }, 10000);
  };
})();
