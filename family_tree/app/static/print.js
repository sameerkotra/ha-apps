/* Family Tree — printable wall chart and family book (§13.5).
   Everything is laid out here in the browser and printed (or saved as PDF)
   from the print dialog; the family comes from POST api/export/print, which
   applies the same privacy and detail choices as every other export. */
"use strict";

const PAPER = { A4: [210, 297], A3: [297, 420], A2: [420, 594], A1: [594, 841], Letter: [216, 279], Tabloid: [279, 432] };
const BOX_W = 170, BOX_H = 64, GAP_X = 18, ROW_H = 120;
const SEX_COLOR = { male: "#3f7fd6", female: "#d0558a", other: "#7d5cc8", unknown: "#8a93a3" };

const esc = UI.escapeHtml;   // common/ui.js

/* The privacy/detail part of the export options, seeded from the last website export. */
async function printOptions() {
  let last = {};
  try { last = (await api("api/export/last?format=site")).options || {}; } catch (e) { /* defaults */ }
  const o = exportMerge(exportDefaults(), last);
  return o;
}

function privacyControls(o, onChange) {
  const living = segmented([["limited", "Names only"], ["full", "Everything"], ["exclude", "Leave out"]], o.living, (k) => { o.living = k; onChange(); });
  const dates = segmented([["full", "Full dates"], ["year", "Years only"], ["none", "No dates"]], o.dates, (k) => { o.dates = k; onChange(); });
  const photos = h("input", { type: "checkbox", checked: o.photos !== "none" });
  photos.addEventListener("change", () => { o.photos = photos.checked ? "all" : "none"; onChange(); });
  const places = h("input", { type: "checkbox", checked: o.places });
  places.addEventListener("change", () => { o.places = places.checked; onChange(); });
  return h("div", null,
    h("div", { class: "form-row" }, h("div", { class: "field" }, "Living people", living.el), h("div", { class: "field" }, "Dates", dates.el)),
    h("div", { class: "chips" }, h("label", { class: "check-row" }, photos, "Photos"), h("label", { class: "check-row" }, places, "Places")),
    h("p", { class: "hint" }, "The other details (events, stories, custom fields, sources…) follow your last website export's choices in ",
      h("button", { type: "button", class: "link-btn", onclick: () => go("export") }, "Export"), ". People marked “keep out of exports” are always left out."));
}

async function loadPrintData(o) {
  return api("api/export/print", { method: "POST", body: { format: "site", options: o } });
}

function familyIndex(data) {
  const parents = {}, unions = {};
  for (const f of data.families) {
    for (const c of f.children) {
      if (!parents[c.id] || c.relation === "birth") parents[c.id] = { fam: f, relation: c.relation };
    }
    for (const p of [f.p1, f.p2]) if (p) (unions[p] = unions[p] || []).push(f);
  }
  return { parents, unions };
}

// ---------- wall chart layout ----------
function layoutChart(data, rootId, type, gens) {
  const idx = familyIndex(data);
  const nodes = [];         // {id, x, y, spouse}
  const links = [];         // [from node, to node] (parent → child)
  let slot = 0;
  // ancestors: root at row 0, parents at row -1 …
  function up(pid, depth) {
    const node = { id: pid, row: -depth };
    nodes.push(node);
    const pf = depth < gens ? idx.parents[pid] : null;
    const ps = pf ? [pf.fam.p1, pf.fam.p2].filter((x) => x && data.people[x]) : [];
    if (!ps.length) { node.x = slot++; return node; }
    const kids = ps.map((x) => up(x, depth + 1));
    kids.forEach((k) => links.push([k, node]));
    node.x = (kids[0].x + kids[kids.length - 1].x) / 2;
    return node;
  }
  function down(pid, depth, existing) {
    const node = existing || { id: pid, row: depth };
    if (!existing) nodes.push(node);
    const fams = depth < gens ? (idx.unions[pid] || []) : [];
    const kidsN = [];
    const spouses = [];
    for (const f of fams) {
      const other = f.p1 === pid ? f.p2 : f.p1;
      if (other && data.people[other]) spouses.push(other);
      for (const c of f.children) if (data.people[c.id]) kidsN.push(down(c.id, depth + 1));
    }
    node.spouses = spouses;
    if (!kidsN.length) { if (node.x === undefined) node.x = slot++; return node; }
    kidsN.forEach((k) => links.push([node, k]));
    const mid = (kidsN[0].x + kidsN[kidsN.length - 1].x) / 2;
    if (node.x === undefined) node.x = mid;
    return node;
  }
  let root;
  if (type === "ancestors") root = up(rootId, 0);
  else if (type === "descendants") root = down(rootId, 0);
  else {
    const a = up(rootId, 0);
    const aNodes = nodes.slice();
    const aSlots = slot;
    slot = 0;
    const saved = nodes.length;
    const d = { id: rootId, row: 0 };
    down(rootId, 0, d);
    const dNodes = nodes.slice(saved);
    // line up the root: shift the smaller half under the other's root
    const shift = a.x - (d.x === undefined ? 0 : d.x);
    for (const n of dNodes) n.x += shift;
    a.spouses = d.spouses;
    // the descendant root duplicates the ancestor root: point its links at it
    for (const l of links) { if (l[0] === d) l[0] = a; }
    root = a;
    void aNodes; void aSlots;
  }
  // normalise
  const minX = Math.min(...nodes.map((n) => n.x)), minRow = Math.min(...nodes.map((n) => n.row));
  const maxX = Math.max(...nodes.map((n) => n.x)), maxRow = Math.max(...nodes.map((n) => n.row));
  for (const n of nodes) { n.px = (n.x - minX) * (BOX_W + GAP_X); n.py = (n.row - minRow) * ROW_H; }
  return { nodes, links, root, width: (maxX - minX) * (BOX_W + GAP_X) + BOX_W, height: (maxRow - minRow) * ROW_H + BOX_H };
}

async function imageData(url) {
  try {
    const res = await fetch(url, { headers: { "X-Device-Id": deviceId() } });
    if (!res.ok) return null;
    const blob = await res.blob();
    return await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => resolve(null); r.readAsDataURL(blob); });
  } catch (e) { return null; }
}

/* The chart as an SVG string. `embed` = images as data: URIs (for the download). */
async function chartSvg(data, lay, opts, embed) {
  const pad = 30, titleH = 70;
  const W = lay.width + pad * 2, H = lay.height + pad * 2 + titleH;
  const photoOf = {};
  if (opts.photos) {
    for (const n of lay.nodes) {
      const p = data.people[n.id];
      if (p && p.photo && !(n.id in photoOf)) {
        const url = `api/media/${encodeURIComponent(p.photo)}/file?size=256${p.photoRegion ? "&region=" + encodeURIComponent(p.photoRegion) : ""}`;
        photoOf[n.id] = embed ? await imageData(url) : url;
      }
    }
  }
  const out = [`<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="Georgia, 'Times New Roman', serif">`,
    `<rect width="${W}" height="${H}" fill="#ffffff"/>`,
    `<text x="${W / 2}" y="${pad + 26}" text-anchor="middle" font-size="28" font-weight="bold" fill="#222">${esc(opts.title)}</text>`,
    `<text x="${W / 2}" y="${pad + 50}" text-anchor="middle" font-size="13" fill="#666">${esc(opts.subtitle)}</text>`,
    `<defs><clipPath id="ph"><circle cx="24" cy="32" r="22"/></clipPath></defs>`,
    `<g transform="translate(${pad},${pad + titleH})">`];
  for (const [a, b] of lay.links) {
    const top = a.row < b.row ? a : b, bot = a.row < b.row ? b : a;
    const x1 = top.px + BOX_W / 2, y1 = top.py + BOX_H, x2 = bot.px + BOX_W / 2, y2 = bot.py, ym = (y1 + y2) / 2;
    out.push(`<path d="M${x1},${y1} V${ym} H${x2} V${y2}" fill="none" stroke="#999" stroke-width="1.4"/>`);
  }
  for (const n of lay.nodes) {
    const p = data.people[n.id];
    if (!p) continue;
    const ph = photoOf[n.id];
    const tx = ph ? 54 : 10;
    const name = p.name.length > (ph ? 20 : 24) ? p.name.slice(0, ph ? 19 : 23) + "…" : p.name;
    out.push(`<g transform="translate(${n.px},${n.py})">`,
      `<rect width="${BOX_W}" height="${BOX_H}" rx="8" fill="${n === lay.root ? "#fff8e6" : "#ffffff"}" stroke="${n === lay.root ? "#c9a227" : "#bbb"}" stroke-width="${n === lay.root ? 2 : 1}"/>`,
      `<rect width="5" height="${BOX_H}" rx="2" fill="${SEX_COLOR[p.gender] || SEX_COLOR.unknown}"/>`,
      ph ? `<image href="${esc(ph)}" xlink:href="${esc(ph)}" x="2" y="10" width="44" height="44" clip-path="url(#ph)" preserveAspectRatio="xMidYMid slice"/>` : "",
      `<text x="${tx}" y="24" font-size="13" font-weight="bold" fill="#111">${esc(name)}</text>`,
      p.years ? `<text x="${tx}" y="41" font-size="11" fill="#555">${esc(p.years)}</text>` : "",
      n.spouses && n.spouses.length && opts.spouses ? `<text x="${tx}" y="56" font-size="10.5" fill="#666">m. ${esc(n.spouses.map((s) => data.people[s].given || data.people[s].name).join(", ").slice(0, 26))}</text>` : "",
      `</g>`);
  }
  out.push("</g></svg>");
  return { svg: out.join(""), width: W, height: H };
}

function pageCss(size, orient, marginMm) {
  const style = h("style", { id: "printPageStyle" }, `@page { size: ${size} ${orient}; margin: ${marginMm}mm; }`);
  const old = document.getElementById("printPageStyle");
  if (old) old.remove();
  document.head.appendChild(style);
}

function printNow(root) {
  const holder = document.getElementById("printRoot") || document.body.appendChild(h("div", { id: "printRoot" }));
  mount(holder, root);
  document.documentElement.classList.add("printing");
  const done = () => { document.documentElement.classList.remove("printing"); clear(holder); window.removeEventListener("afterprint", done); };
  window.addEventListener("afterprint", done);
  setTimeout(() => { window.print(); setTimeout(() => { if (!window.matchMedia || !window.matchMedia("print").matches) done(); }, 1500); }, 400);
}

async function viewPrintChart() {
  const o = await printOptions();
  const meId = state.user.mePersonId;
  let chosen = meId ? { id: meId, name: state.user.mePersonName || "Me" } : null;
  const picker = personPicker({ placeholder: "Start from…", initial: chosen, onChange: (p) => { chosen = p; refresh(); } });
  const saved0 = (() => { try { return JSON.parse(lsGet("chartSetup") || "{}"); } catch (e) { return {}; } })();
  const type = segmented([["ancestors", "Ancestors"], ["descendants", "Descendants"], ["hourglass", "Hourglass"]], saved0.type || "ancestors", () => refresh());
  const gens = h("select", { "aria-label": "Generations" }, ...[2, 3, 4, 5, 6, 7, 8, 10].map((n) => h("option", { value: String(n) }, `${n} generations`)));
  gens.value = String(saved0.gens || 4);
  gens.addEventListener("change", () => refresh());
  const paper = h("select", { "aria-label": "Paper" }, ...Object.keys(PAPER).map((k) => h("option", { value: k }, k)), h("option", { value: "poster" }, "Poster: tiled A4 sheets"));
  paper.value = saved0.paper || "A3";
  const orient = segmented([["landscape", "Landscape"], ["portrait", "Portrait"]], saved0.orient || "landscape", () => drawInfo());
  const cols = h("select", { "aria-label": "Sheets across" }, ...[2, 3, 4, 5, 6, 8].map((n) => h("option", { value: String(n) }, `${n} sheets across`)));
  cols.value = String(saved0.cols || 3);
  const spouses = h("input", { type: "checkbox", checked: saved0.spouses !== false });
  spouses.addEventListener("change", () => refresh());
  const title = h("input", { type: "text", maxlength: "120", value: saved0.title || "" , placeholder: "Title (default: The … family tree)" });
  const posterBox = h("div", { class: "form-row" }, field("Sheets across", cols));
  const info = h("div", { class: "hint" });
  posterBox.style.display = paper.value === "poster" ? "" : "none";
  const preview = h("div", { class: "chart-preview" }, spinner());
  let data = null, lay = null, dataKey = null;
  const persist = () => lsSet("chartSetup", JSON.stringify({ type: type.get(), gens: +gens.value, paper: paper.value, orient: orient.get(), cols: +cols.value, spouses: spouses.checked, title: title.value }));
  const titleText = () => title.value.trim() || (chosen ? `The ${(data && data.people[chosen.id] && data.people[chosen.id].surname) || chosen.name.split(" ").slice(-1)[0]} family tree` : "Family tree");
  const subtitle = () => `${{ ancestors: "Ancestors", descendants: "Descendants", hourglass: "Ancestors and descendants" }[type.get()]} of ${data && data.people[chosen.id] ? data.people[chosen.id].name : chosen.name} · ${new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })}`;
  function drawInfo() {
    posterBox.style.display = paper.value === "poster" ? "" : "none";
    if (!lay) return;
    if (paper.value === "poster") {
      const n = +cols.value, pw = 190, ph = 277, ov = 10;
      const totalW = n * (pw - ov) + ov;
      const totalH = totalW * (lay.height + 170) / (lay.width + 60);
      const rows = Math.max(1, Math.ceil((totalH - ov) / (ph - ov)));
      info.textContent = `${n} × ${rows} = ${n * rows} A4 sheets, about ${Math.round(totalW / 10)} × ${Math.round(totalH / 10)} cm, with 1 cm overlap and cut marks.`;
    } else {
      const [a, b] = PAPER[paper.value];
      const [pw, ph] = orient.get() === "landscape" ? [b, a] : [a, b];
      const scale = Math.min((pw - 20) / (lay.width + 60), (ph - 20) / (lay.height + 170));
      const boxMm = BOX_W * scale;
      info.textContent = `${lay.nodes.length} people. Each box will be about ${boxMm.toFixed(0)} mm wide` + (boxMm < 22 ? " — too small to read; choose bigger paper, fewer generations or a poster." : ".");
    }
  }
  async function refresh() {
    persist();
    if (!chosen) { mount(preview, h("div", { class: "empty" }, "Choose who the chart starts from.")); return; }
    const t = type.get() === "hourglass" ? "both" : type.get();
    const key = JSON.stringify([chosen.id, t, gens.value, o.living, o.dates, o.photos, o.places]);
    try {
      if (key !== dataKey) {
        mount(preview, spinner());
        const opts = { ...o, scope: { ...o.scope, type: t, personId: chosen.id, generations: +gens.value, partners: spouses.checked } };
        data = await loadPrintData(opts);
        dataKey = key;
      }
      if (!data.people[chosen.id]) { mount(preview, h("div", { class: "empty" }, "That person isn't included with these privacy choices.")); return; }
      lay = layoutChart(data, chosen.id, type.get(), +gens.value);
      const c = await chartSvg(data, lay, { title: titleText(), subtitle: subtitle(), photos: o.photos !== "none", spouses: spouses.checked }, false);
      const holder = h("div", { class: "chart-svg" });
      holder.innerHTML = c.svg;
      mount(preview, holder, data.warnings && data.warnings.length ? h("div", { class: "warnings" }, ...data.warnings.map((w) => h("div", null, "⚠ " + w))) : null);
      drawInfo();
    } catch (e) { mount(preview, h("div", { class: "error-text" }, e.message)); }
  }
  paper.addEventListener("change", () => { persist(); drawInfo(); });
  cols.addEventListener("change", () => { persist(); drawInfo(); });
  title.addEventListener("change", () => refresh());
  const doPrint = async () => {
    if (!lay) return;
    persist();
    const c = await chartSvg(data, lay, { title: titleText(), subtitle: subtitle(), photos: o.photos !== "none", spouses: spouses.checked }, false);
    if (paper.value === "poster") {
      pageCss("A4", "portrait", 10);
      const n = +cols.value, pw = 190, ph = 277, ov = 10;
      const totalW = n * (pw - ov) + ov;
      const totalH = totalW * c.height / c.width;
      const rows = Math.max(1, Math.ceil((totalH - ov) / (ph - ov)));
      const sheets = [];
      for (let r = 0; r < rows; r++) for (let q = 0; q < n; q++) {
        const inner = h("div", { class: "tile-inner", style: `width:${totalW}mm;height:${totalH}mm;left:${-q * (pw - ov)}mm;top:${-r * (ph - ov)}mm` });
        inner.innerHTML = c.svg;
        const svg = inner.querySelector("svg");
        svg.setAttribute("width", "100%"); svg.setAttribute("height", "100%");
        sheets.push(h("div", { class: "tile", style: `width:${pw}mm;height:${ph}mm` }, inner,
          q > 0 ? h("div", { class: "cut cut-l", style: `left:${ov}mm` }) : null, r > 0 ? h("div", { class: "cut cut-t", style: `top:${ov}mm` }) : null,
          q > 0 ? h("div", { class: "overlap overlap-l", style: `width:${ov}mm` }) : null, r > 0 ? h("div", { class: "overlap overlap-t", style: `height:${ov}mm` }) : null,
          h("div", { class: "tile-label" }, `Row ${r + 1} of ${rows}, sheet ${q + 1} of ${n}` + (q > 0 || r > 0 ? " — cut on the dashed line, lay over the sheet before" : ""))));
      }
      printNow(h("div", { class: "print-tiles" }, ...sheets));
    } else {
      pageCss(paper.value === "Tabloid" ? "11in 17in" : paper.value, orient.get(), 10);
      const holder = h("div", { class: "print-chart" });
      holder.innerHTML = c.svg;
      const svg = holder.querySelector("svg");
      svg.removeAttribute("width"); svg.removeAttribute("height");
      printNow(holder);
    }
  };
  const download = async () => {
    if (!lay) return;
    const c = await chartSvg(data, lay, { title: titleText(), subtitle: subtitle(), photos: o.photos !== "none", spouses: spouses.checked }, true);
    const blob = new Blob([`<?xml version="1.0" encoding="UTF-8"?>\n` + c.svg], { type: "image/svg+xml" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `family-chart-${new Date().toISOString().slice(0, 10)}.svg` });
    document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
  };
  setTimeout(refresh, 0);
  return h("div", null, h("h2", { class: "page-title" }, "🖼️ Wall chart"),
    h("div", { class: "card" },
      h("div", { class: "field wide" }, "Start from", picker.el),
      h("div", { class: "form-row" }, h("div", { class: "field" }, "Chart", type.el), field("Depth", gens)),
      h("div", { class: "form-row" }, field("Paper", paper), h("div", { class: "field" }, "Orientation", orient.el)),
      posterBox,
      h("div", { class: "form-row" }, field("Title", title, "wide")),
      h("label", { class: "check-row" }, spouses, "Show partners' names"),
      h("hr", { class: "sep" }),
      privacyControls(o, () => refresh()),
      info,
      h("div", { class: "actions" },
        h("button", { type: "button", class: "btn-secondary", onclick: download }, "⬇ Download SVG"),
        h("button", { type: "button", class: "btn-primary", onclick: doPrint }, "🖨 Print or save as PDF"))),
    h("div", { class: "card" }, preview));
}

// ---------- family book ----------
function bookOrder(data, rootId) {
  const idx = familyIndex(data);
  const gen = {};
  if (rootId && data.people[rootId]) {
    gen[rootId] = 0;
    const q = [rootId];
    while (q.length) {
      const cur = q.shift();
      const pf = idx.parents[cur];
      const next = [];
      if (pf) for (const p of [pf.fam.p1, pf.fam.p2]) if (p) next.push([p, gen[cur] - 1]);
      for (const f of idx.unions[cur] || []) {
        for (const p of [f.p1, f.p2]) if (p && p !== cur) next.push([p, gen[cur]]);
        for (const c of f.children) next.push([c.id, gen[cur] + 1]);
      }
      for (const [id, g] of next) if (data.people[id] && !(id in gen)) { gen[id] = g; q.push(id); }
    }
  }
  const year = (p) => (p.birth && p.birth.year) || 9999;
  return Object.values(data.people).filter((p) => !p.placeholder)
    .sort((a, b) => ((gen[a.id] ?? 99) - (gen[b.id] ?? 99)) || (year(a) - year(b)) || a.name.localeCompare(b.name))
    .map((p) => p.id);
}

function bookHtml(data, order, opts) {
  const num = {};
  order.forEach((id, i) => { num[id] = i + 1; });
  const idx = familyIndex(data);
  const ref = (id) => { const p = data.people[id]; if (!p) return ""; return num[id] ? `${esc(p.name)} <span class="ref">§${num[id]}</span>` : esc(p.name); };
  const img = (mid, region, size = 1024) => `api/media/${encodeURIComponent(mid)}/file?size=${size}${region ? "&region=" + encodeURIComponent(region) : ""}`;
  const parts = [];
  parts.push(`<section class="book-title"><h1>${esc(opts.title)}</h1>${opts.subtitle ? `<p class="sub">${esc(opts.subtitle)}</p>` : ""}` +
    (opts.cover ? `<img class="cover" src="${img(opts.cover.photo, opts.cover.photoRegion, 1024)}" alt="">` : "") +
    `<p class="date">${esc(new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" }))} · ${order.length} people</p></section>`);
  parts.push(`<section class="book-toc"><h2>Contents</h2><ol>${order.map((id) => `<li><span>${esc(data.people[id].name)}</span><span class="dots"></span><span>§${num[id]}</span></li>`).join("")}` +
    `</ol>${opts.gallery ? "<p>Photo gallery · Index of names</p>" : "<p>Index of names</p>"}</section>`);
  for (const id of order) {
    const p = data.people[id];
    const pf = idx.parents[id];
    const ps = pf ? [pf.fam.p1, pf.fam.p2].filter((x) => x && data.people[x]) : [];
    const lines = [];
    const at = (ev) => ev.date && ev.time ? `${ev.date} at ${ev.time}` : ev.date;
    if (p.birth && (p.birth.date || p.birth.place)) lines.push("Born " + [at(p.birth), p.birth.place].filter(Boolean).map(esc).join(" in "));
    if (p.death && (p.death.date || p.death.place)) lines.push("Died " + [at(p.death), p.death.place].filter(Boolean).map(esc).join(" in "));
    const aka = [p.birthSurname ? `née ${p.birthSurname}` : "", p.nickname ? `“${p.nickname}”` : "", ...(p.otherNames || [])].filter(Boolean);
    const fams = (idx.unions[id] || []).map((f) => {
      const other = f.p1 === id ? f.p2 : f.p1;
      const m = (f.events || []).find((e) => e.type === "marriage");
      const kids = f.children.filter((c) => data.people[c.id]).map((c) => `<li>${ref(c.id)}</li>`).join("");
      return `<div class="book-fam"><div>${other && data.people[other] ? `With ${ref(other)}` : "Partner unknown"}${m && (m.date || m.place) ? ` — married ${[m.date, m.place].filter(Boolean).map(esc).join(", ")}` : ""}</div>${kids ? `<ul>${kids}</ul>` : ""}</div>`;
    }).join("");
    const events = (p.events || []).filter((e) => !["birth", "death"].includes(e.type));
    parts.push(`<section class="book-person${opts.newPage ? " new-page" : ""}" id="p-${esc(id)}">` +
      `<div class="book-head">${p.photo && opts.photos ? `<img src="${img(p.photo, p.photoRegion, 256)}" alt="">` : ""}<div><div class="num">§${num[id]}</div><h2>${esc(p.name)}</h2>` +
      (aka.length ? `<div class="dim">${esc(aka.join(" · "))}</div>` : "") + (lines.length ? `<div>${lines.join(" · ")}</div>` : "") +
      (p.relationship ? `<div class="dim">${esc(p.relationship)}${data.relativeToName ? " of " + esc(data.relativeToName) : ""}</div>` : "") + `</div></div>` +
      (p.limited ? `<p class="dim">Details of living people aren't included.</p>` : "") +
      ((p.custom || []).length ? `<div class="book-kv">${p.custom.map((c) => `<div><b>${esc(c.label)}:</b> ${esc(c.value)}</div>`).join("")}</div>` : "") +
      (ps.length ? `<h3>Parents</h3><div>${ps.map(ref).join(" and ")}</div>` : "") +
      (fams ? `<h3>Partners and children</h3>${fams}` : "") +
      (events.length ? `<h3>Life</h3><table class="book-events">${events.map((e) => `<tr><td>${esc(e.date || "")}</td><td><b>${esc(e.label)}</b>${e.title ? " — " + esc(e.title) : ""}${e.place ? `<div class="dim">${esc(e.place)}</div>` : ""}${e.description ? `<div class="dim">${esc(e.description)}</div>` : ""}</td></tr>`).join("")}</table>` : "") +
      (p.biography ? `<h3>Biography</h3><div class="book-text">${esc(p.biography)}</div>` : "") +
      (p.stories || []).map((s) => `<h3>${esc(s.title)}</h3><div class="book-text">${esc(s.body)}</div>`).join("") +
      ((p.sources || []).length ? `<h3>Sources</h3><ol class="book-sources">${p.sources.map((x) => `<li>${esc(x.title)}${x.page ? ", " + esc(x.page) : ""}${x.fact ? ` <span class="dim">— ${esc(x.fact)}</span>` : ""}</li>`).join("")}</ol>` : "") +
      `</section>`);
  }
  if (opts.gallery) {
    const photos = Object.values(data.media).slice(0, 300);
    if (photos.length) {
      parts.push(`<section class="book-gallery new-page"><h2>Photo gallery</h2><div class="grid">${photos.map((m) =>
        `<figure><img src="${img(m.id, null, 1024)}" alt=""><figcaption>${esc([m.title, m.date].filter(Boolean).join(" · "))}` +
        `${m.tags && m.tags.length ? `<div class="dim">${m.tags.filter((t) => data.people[t]).map(ref).join(", ")}</div>` : ""}</figcaption></figure>`).join("")}</div></section>`);
    }
  }
  const names = order.map((id) => data.people[id]).sort((a, b) => (a.surname || "~").localeCompare(b.surname || "~") || (a.given || "").localeCompare(b.given || ""));
  parts.push(`<section class="book-index new-page"><h2>Index of names</h2><div class="cols">${names.map((p) =>
    `<div>${esc(p.surname ? `${p.surname}, ${p.given || ""}` : p.name)} <span class="ref">§${num[p.id]}</span></div>`).join("")}</div></section>`);
  return parts.join("");
}

async function viewPrintBook() {
  const o = await printOptions();
  const meId = state.user.mePersonId;
  let chosen = meId ? { id: meId, name: state.user.mePersonName || "Me" } : null;
  const saved0 = (() => { try { return JSON.parse(lsGet("bookSetup") || "{}"); } catch (e) { return {}; } })();
  const who = segmented([["both", "Ancestors and descendants"], ["ancestors", "Ancestors"], ["descendants", "Descendants"], ["whole", "Everyone"]], saved0.who || "both", () => { box.style.display = who.get() === "whole" ? "none" : ""; });
  const picker = personPicker({ placeholder: "Centred on…", initial: chosen, onChange: (p) => { chosen = p; } });
  const gens = h("select", { "aria-label": "Generations" }, ...[1, 2, 3, 4, 5, 6, 8, 10].map((n) => h("option", { value: String(n) }, `${n} generation${n > 1 ? "s" : ""}`)));
  gens.value = String(saved0.gens || 3);
  const box = h("div", { class: "form-row" }, h("div", { class: "field wide" }, "Centred on", picker.el), field("How far", gens));
  box.style.display = who.get() === "whole" ? "none" : "";
  const title = h("input", { type: "text", maxlength: "120", value: saved0.title || "", placeholder: "e.g. The Sharma family" });
  const subtitle = h("input", { type: "text", maxlength: "200", value: saved0.subtitle || "", placeholder: "optional" });
  const newPage = h("input", { type: "checkbox", checked: saved0.newPage !== false });
  const gallery = h("input", { type: "checkbox", checked: saved0.gallery !== false });
  const status = h("div", { class: "hint" });
  const make = async (printIt) => {
    if (who.get() !== "whole" && !chosen) { toast("Choose who the book is centred on.", { error: true }); return; }
    lsSet("bookSetup", JSON.stringify({ who: who.get(), gens: +gens.value, title: title.value, subtitle: subtitle.value, newPage: newPage.checked, gallery: gallery.checked }));
    status.textContent = "Gathering the family…";
    try {
      const scope = who.get() === "whole" ? { ...o.scope, type: "whole" } : { ...o.scope, type: who.get(), personId: chosen.id, generations: +gens.value, partners: true };
      const data = await loadPrintData({ ...o, scope });
      const root = chosen && data.people[chosen.id] ? chosen.id : data.home;
      const order = bookOrder(data, root);
      const rp = root ? data.people[root] : null;
      const html = bookHtml(data, order, {
        title: title.value.trim() || `The ${(rp && rp.surname) || ""} family`.replace("The  family", "Our family"),
        subtitle: subtitle.value.trim(), photos: o.photos !== "none", gallery: gallery.checked && o.photos !== "none", newPage: newPage.checked,
        cover: rp && rp.photo && o.photos !== "none" ? rp : null });
      status.textContent = `${order.length} people${data.warnings.length ? " · " + data.warnings.join(" ") : ""}.`;
      const doc = h("div", { class: "book" });
      doc.innerHTML = html;
      if (printIt) { pageCss("A4", "portrait", 15); printNow(doc); }
      else mount(previewBox, doc);
    } catch (e) { status.textContent = ""; fail(e); }
  };
  const previewBox = h("div", { class: "book-preview" });
  return h("div", null, h("h2", { class: "page-title" }, "📖 Family book"),
    h("div", { class: "card" },
      h("div", { class: "field wide" }, "Who's in it", who.el), box,
      h("div", { class: "form-row" }, field("Title", title), field("Subtitle", subtitle)),
      h("div", { class: "chips" }, h("label", { class: "check-row" }, newPage, "Each person on a new page"), h("label", { class: "check-row" }, gallery, "Photo gallery at the end")),
      h("hr", { class: "sep" }), privacyControls(o, () => {}),
      h("p", { class: "hint" }, "People are numbered (§1, §2…) in the contents; parents, partners, children and the index point to those numbers. Print, or choose “Save as PDF” in the print dialog."),
      status,
      h("div", { class: "actions" }, h("button", { type: "button", class: "btn-secondary", onclick: () => make(false) }, "Preview"),
        h("button", { type: "button", class: "btn-primary", onclick: () => make(true) }, "🖨 Print or save as PDF"))),
    previewBox);
}
