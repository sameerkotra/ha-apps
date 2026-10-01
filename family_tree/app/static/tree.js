"use strict";
/* Family Tree — the hourglass chart (§11). Plain SVG built with DOM calls, its
   own layered layout, and pointer/wheel pan-zoom. No libraries.

   Layout: every generation is a row. Descendants are measured bottom-up so
   each person's block is at least as wide as all their children's blocks,
   and children are centred under their family. Ancestors are laid out as a
   pedigree above the focus person and shifted so the focus lines up. Blocks
   never overlap because every block owns its own horizontal span. */
(function () {
  const SVGNS = "http://www.w3.org/2000/svg";
  const CW = 164, CH = 66, HG = 22, CG = 14, VG = 64;   // card w/h, gaps between cards, couples, rows
  const ROW = CH + VG;

  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
    for (const k of kids) if (k !== null && k !== undefined) el.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    return el;
  }

  function initials(p) {
    const a = (p.given || p.name || "?").trim()[0] || "?";
    const b = (p.surname || "").trim()[0] || "";
    return (a + b).toUpperCase();
  }

  function fit(text, max) {
    if (!text) return "";
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  // ---------- measuring & placing ----------
  function measureDown(node, ghosts) {
    // the person plus their partners side by side; one family group per partner
    const fams = node.families || [];
    const partnersShown = fams.filter((f) => f.partner).length;
    const extra = ghosts && ghosts.partner ? 1 : 0;
    node._cluster = CW * (1 + partnersShown + extra) + CG * (partnersShown + extra);
    let kidsW = 0, groups = 0;
    for (const f of fams) {
      const kids = f.children || [];
      const kidList = kids.slice();
      if (ghosts && ghosts.child === f.id) kidList.push({ ghost: "child", familyId: f.id });
      f._kids = kidList;
      if (!kidList.length) { f._w = 0; continue; }
      let w = 0;
      kidList.forEach((k, i) => { w += (k.ghost ? CW : measureDown(k)) + (i ? HG : 0); });
      f._w = w;
      kidsW += w + (groups ? HG * 2 : 0);
      groups++;
    }
    if (ghosts && ghosts.child === "new" ) {
      node._loneGhost = true;
      kidsW = Math.max(kidsW, CW);
    }
    node._kidsW = kidsW;
    node._w = Math.max(node._cluster, kidsW);
    return node._w;
  }

  function placeDown(node, left, y, out, ghosts) {
    const clusterLeft = left + (node._w - node._cluster) / 2;
    const fams = node.families || [];
    // With two or more partners the second one sits on the person's LEFT, so each
    // couple has its own gap to drop its children's line from (a line through a
    // partner's card would make that partner look like the other parent).
    const leftCount = fams.filter((f) => f.partner).length >= 2 ? 1 : 0;
    const personX = clusterLeft + leftCount * (CW + CG);
    const me = { p: node.p, x: personX, y, relation: node.relation };
    out.cards.push(me);
    let px = personX + CW + CG, nth = 0, right = 0;
    const famAnchors = [];
    for (const f of fams) {
      if (f.partner) {
        let a;
        if (nth === 1 && leftCount) {
          out.cards.push({ p: f.partner, x: clusterLeft, y, partnerOf: node.p.id, family: f });
          a = { f, x1: clusterLeft + CW, x2: personX, dropX: clusterLeft + CW + CG / 2, off: 0 };
        } else {
          out.cards.push({ p: f.partner, x: px, y, partnerOf: node.p.id, family: f });
          a = { f, x1: personX + CW, x2: px, dropX: px - CG / 2, off: right * 5 };
          px += CW + CG;
          right++;
        }
        nth++;
        famAnchors.push(a);
      } else {
        famAnchors.push({ f, x1: null, x2: null, dropX: personX + CW / 2, off: 0 });
      }
    }
    if (ghosts && ghosts.partner) {
      const open = fams.find((f) => !f.partner);        // fill a family's empty partner slot first
      out.cards.push({ ghost: "partner", x: px, y, anchor: node.p.id, familyId: open ? open.id : null });
    }
    // couple lines
    famAnchors.forEach((a) => {
      if (a.x1 !== null) {
        const ly = y + CH / 2 + a.off;
        out.lines.push({ d: `M${a.x1},${ly} H${a.x2}`, cls: a.f.ended ? "couple ended" : "couple" });
      }
    });
    // children, each family's group in the left-to-right order of its drop line
    famAnchors.sort((p, q) => p.dropX - q.dropX);
    let cx = left + (node._w - node._kidsW) / 2;
    const cy = y + ROW;
    let first = true;
    // several families with children each get their own bar height, so two bars
    // that overlap sideways never read as one family
    const groups = famAnchors.filter((a) => a.f._kids && a.f._kids.length).length;
    let gi = 0;
    for (const a of famAnchors) {
      const f = a.f;
      if (!f._kids || !f._kids.length) continue;
      if (!first) cx += HG * 2;
      first = false;
      const tops = [];
      let kx = cx;
      for (const k of f._kids) {
        if (k.ghost) {
          out.cards.push({ ghost: "child", x: kx, y: cy, familyId: f.id, anchor: node.p.id });
          tops.push(kx + CW / 2);
          kx += CW + HG;
        } else {
          const kl = kx;
          placeDown(k, kl, cy, out);
          tops.push(k._cx);
          kx += k._w + HG;
        }
      }
      cx += f._w;
      const fromX = a.dropX;
      const fromY = a.x1 !== null ? y + CH / 2 + a.off : y + CH;
      const barY = y + CH + VG / 2 + (gi++ - (groups - 1) / 2) * Math.min(10, (VG - 20) / Math.max(groups - 1, 1));
      const minX = Math.min(...tops, fromX), maxX = Math.max(...tops, fromX);
      out.lines.push({ d: `M${fromX},${fromY} V${barY}`, cls: "desc" });
      out.lines.push({ d: `M${minX},${barY} H${maxX}`, cls: "desc" });
      f._kids.forEach((k, i) => {
        out.lines.push({ d: `M${tops[i]},${barY} V${cy}`, cls: k.relation && k.relation !== "birth" ? "desc nonbirth" : "desc" });
      });
    }
    if (node._loneGhost) {
      const gx = clusterLeft + (node._cluster - CW) / 2;
      out.cards.push({ ghost: "child", x: gx, y: cy, anchor: node.p.id });
      out.lines.push({ d: `M${personX + CW / 2},${y + CH} V${cy}`, cls: "desc ghostline" });
    }
    node._cx = personX + CW / 2;
  }

  function measureUp(node) {
    const par = node.parents;
    if (!par) { node._w = CW; return CW; }
    const wa = par.a ? measureUp(par.a) : 0;
    const wb = par.b ? measureUp(par.b) : 0;
    node._w = Math.max(CW, wa + wb + (wa && wb ? HG : 0));
    return node._w;
  }

  function placeUp(node, left, y, out, isFocus, focusId) {
    if (node.ghost) {                               // an empty "+ Add father / mother" slot
      out.cards.push({ ghost: node.ghost, x: left, y, familyId: node.familyId, anchor: focusId });
      return left + CW / 2;
    }
    const par = node.parents;
    let cx;
    if (!par || (!par.a && !par.b)) {
      cx = left + node._w / 2;
    } else {
      const inner = (par.a ? par.a._w : 0) + (par.b ? par.b._w : 0) + (par.a && par.b ? HG : 0);
      let l = left + (node._w - inner) / 2;
      const py = y - ROW;
      const xs = [];
      for (const side of [par.a, par.b]) {
        if (!side) continue;
        xs.push(placeUp(side, l, py, out, false, focusId));
        l += side._w + HG;
      }
      cx = xs.length === 2 ? (xs[0] + xs[1]) / 2 : xs[0];
      const barY = y - VG / 2;
      if (xs.length === 2) {
        out.lines.push({ d: `M${xs[0] + CW / 2},${py + CH / 2} H${xs[1] - CW / 2}`, cls: "couple" });
        out.lines.push({ d: `M${cx},${py + CH / 2} V${barY}`, cls: "anc", famDrop: isFocus });
      } else {
        out.lines.push({ d: `M${xs[0]},${py + CH} V${barY}`, cls: "anc", famDrop: isFocus });
      }
      if (!isFocus) out.lines.push({ d: `M${cx},${barY} V${y}`, cls: par.relation !== "birth" ? "anc nonbirth" : "anc" });
      node._barY = barY;
      node._parentMid = cx;
    }
    if (!isFocus) out.cards.push({ p: node.p, x: cx - CW / 2, y, repeat: node.repeat, more: node.moreParents, truncatedUp: node.truncated });
    return cx;
  }

  // ---------- layout of the whole hourglass ----------
  function layout(data, opts) {
    const out = { cards: [], lines: [] };
    const focusId = data.focus;
    const desc = data.descendants;
    const ghosts = opts.editable ? {
      partner: true,
      child: (desc.families && desc.families.length) ? desc.families[0].id : "new",
    } : null;
    measureDown(desc, ghosts);
    // siblings: older to the left, younger to the right of the focus block
    const sibs = data.siblings || [];
    const older = sibs.filter((x) => x.older), younger = sibs.filter((x) => !x.older);
    let x = 0;
    const sibCards = [];
    for (const sb of older) { sibCards.push({ p: sb, x, y: 0, sibling: true }); x += CW + HG; }
    const focusLeft = x;
    placeDown(desc, focusLeft, 0, out, ghosts);
    x = focusLeft + desc._w + HG;
    for (const sb of younger) { sibCards.push({ p: sb, x, y: 0, sibling: true }); x += CW + HG; }
    out.cards.push(...sibCards);
    const focusCx = desc._cx;

    // ancestors, with ghost parents for the focus person
    const anc = data.ancestors;
    if (opts.editable) {
      if (!anc.parents) {
        // no ghosts when parents exist but are just outside the chosen depth (Up = 0)
        if (!anc.truncated) anc.parents = { a: { ghost: "father" }, b: { ghost: "mother" }, relation: "birth" };
      } else {
        const kindFor = (other) => (other && other.p && other.p.gender === "male" ? "mother"
          : other && other.p && other.p.gender === "female" ? "father" : "parent");
        if (!anc.parents.a) anc.parents.a = { ghost: kindFor(anc.parents.b), familyId: anc.parents.familyId };
        if (!anc.parents.b) anc.parents.b = { ghost: kindFor(anc.parents.a), familyId: anc.parents.familyId };
      }
    }
    const tmp = { cards: [], lines: [] };
    measureUp(anc);
    const cxUp = placeUp(anc, 0, 0, tmp, true, focusId);
    const dx = focusCx - cxUp;
    for (const c of tmp.cards) {
      c.x += dx;
      out.cards.push(c);
    }
    for (const l of tmp.lines) out.lines.push(shiftLine(l, dx));
    // one bar joining the focus's parents to the focus and full siblings
    if (anc.parents && (anc._parentMid !== undefined)) {
      const barY = -VG / 2;
      const mid = anc._parentMid + dx;
      const tops = [focusCx, ...sibCards.map((c) => c.x + CW / 2)];
      const minX = Math.min(mid, ...tops), maxX = Math.max(mid, ...tops);
      out.lines.push({ d: `M${minX},${barY} H${maxX}`, cls: "anc" });
      out.lines.push({ d: `M${focusCx},${barY} V0`, cls: anc.parents.relation !== "birth" ? "anc nonbirth" : "anc" });
      for (const c of sibCards) out.lines.push({ d: `M${c.x + CW / 2},${barY} V0`, cls: c.p.full ? "anc" : "anc half" });
    }
    return out;
  }

  // ---------- the whole tree: everyone, one row per generation ----------
  /* Units are the cards that sit side by side on a row: a person with their
     partners. Rows are ordered by a depth-first walk from the top, then
     improved by a few sweeps that pull each unit under its parents and over
     its children. Positions come from an isotonic fit (pool adjacent
     violators): as close as possible to where a unit wants to be, never
     overlapping its neighbour. */
  function pav(desired, widths, gap) {
    // minimise sum (x_i - d_i)^2 subject to x_{i+1} >= x_i + w_i + gap
    const n = desired.length, off = new Array(n);
    let acc = 0;
    for (let i = 0; i < n; i++) { off[i] = acc; acc += widths[i] + gap; }
    const blocks = [];   // {sum, count, start}
    for (let i = 0; i < n; i++) {
      blocks.push({ sum: desired[i] - off[i], count: 1, start: i });
      while (blocks.length > 1) {
        const b = blocks[blocks.length - 1], a = blocks[blocks.length - 2];
        if (a.sum / a.count <= b.sum / b.count) break;
        a.sum += b.sum; a.count += b.count;
        blocks.pop();
      }
    }
    const x = new Array(n);
    for (const b of blocks) {
      const v = b.sum / b.count;
      for (let i = b.start; i < b.start + b.count; i++) x[i] = v + off[i];
    }
    return x;
  }

  function layoutAll(data) {
    const out = { cards: [], lines: [] };
    const P = {};
    data.people.forEach((p) => { P[p.id] = p; });
    const fams = data.families;
    const partnerFams = {}, parentFams = {};
    fams.forEach((f) => {
      [f.p1, f.p2].forEach((x) => { if (x) (partnerFams[x] = partnerFams[x] || []).push(f); });
      f.children.forEach((c) => { (parentFams[c.id] = parentFams[c.id] || []).push(f); });
    });
    const comps = {};
    data.people.forEach((p) => { (comps[p.comp] = comps[p.comp] || []).push(p); });
    let offsetX = 0;
    const GAP = HG;
    for (const key of Object.keys(comps).map(Number).sort((a, b) => a - b)) {
      const members = comps[key];
      // ---- units: partners on the same row grouped together ----
      const unitOf = {}, units = [];
      for (const p of members) {
        if (unitOf[p.id]) continue;
        const group = [], stack = [p.id];
        const seen = new Set([p.id]);
        while (stack.length) {
          const cur = stack.pop();
          group.push(cur);
          for (const f of partnerFams[cur] || []) {
            const o = f.p1 === cur ? f.p2 : f.p1;
            if (o && !seen.has(o) && P[o] && P[o].gen === P[cur].gen && !unitOf[o]) { seen.add(o); stack.push(o); }
          }
        }
        // order: walk the partner chain from an end (someone with one partner in the group)
        const deg = (id) => (partnerFams[id] || []).filter((f) => group.includes(f.p1 === id ? f.p2 : f.p1)).length;
        let startId = group.find((id) => deg(id) <= 1) || group[0];
        const ordered = [], vis = new Set();
        const walk = (id) => {
          if (vis.has(id)) return;
          vis.add(id); ordered.push(id);
          for (const f of partnerFams[id] || []) { const o = f.p1 === id ? f.p2 : f.p1; if (o && group.includes(o)) walk(o); }
        };
        walk(startId);
        group.forEach((id) => { if (!vis.has(id)) ordered.push(id); });
        // men first in a simple couple, like the focus chart
        if (ordered.length === 2 && P[ordered[0]].gender === "female" && P[ordered[1]].gender === "male") ordered.reverse();
        const u = { ids: ordered, gen: P[ordered[0]].gen, w: ordered.length * CW + (ordered.length - 1) * CG, x: 0 };
        ordered.forEach((id) => { unitOf[id] = u; });
        units.push(u);
      }
      const rows = {};
      units.forEach((u) => { (rows[u.gen] = rows[u.gen] || []).push(u); });
      const gens = Object.keys(rows).map(Number).sort((a, b) => a - b);
      const cardX = (id) => { const u = unitOf[id]; return u.x + u.ids.indexOf(id) * (CW + CG); };
      const famAnchorX = (f) => {
        const a = f.p1 && unitOf[f.p1] ? cardX(f.p1) + CW / 2 : null;
        const b = f.p2 && unitOf[f.p2] ? cardX(f.p2) + CW / 2 : null;
        return a !== null && b !== null ? (a + b) / 2 : (a !== null ? a : b);
      };
      const childUnits = (u) => {
        const res = [];
        u.ids.forEach((id) => (partnerFams[id] || []).forEach((f) => f.children.forEach((c) => { if (unitOf[c.id] && !res.includes(unitOf[c.id])) res.push(unitOf[c.id]); })));
        return res;
      };
      const parentUnits = (u) => {
        const res = [];
        u.ids.forEach((id) => (parentFams[id] || []).forEach((f) => [f.p1, f.p2].forEach((x) => { if (x && unitOf[x] && !res.includes(unitOf[x])) res.push(unitOf[x]); })));
        return res;
      };
      // ---- initial order: depth-first from the top rows ----
      const order = {};
      gens.forEach((g) => { order[g] = []; });
      const visited = new Set();
      // `via` remembers the family a unit was first reached from: a married couple
      // is placed under that one (the blood line), and the in-law's parents are
      // pulled over to them instead of the couple being pulled half-way across.
      const visit = (u, via) => {
        if (visited.has(u)) return;
        visited.add(u);
        u.via = via || null;
        parentUnits(u).forEach((pu) => { if (!visited.has(pu) && pu.gen < u.gen) visit(pu, null); });
        order[u.gen].push(u);
        u.ids.forEach((id) => (partnerFams[id] || []).forEach((f) => f.children.forEach((c) => {
          if (unitOf[c.id]) visit(unitOf[c.id], f);
        })));
      };
      gens.forEach((g) => rows[g].forEach(visit));
      gens.forEach((g) => { let x = 0; order[g].forEach((u) => { u.x = x; x += u.w + GAP; }); });
      // ---- sweeps ----
      const downWant = (u) => {
        const xs = [];
        u.ids.forEach((id, i) => (parentFams[id] || []).forEach((f) => {
          if (u.via && f !== u.via) return;
          const ax = famAnchorX(f);
          if (ax !== null) xs.push(ax - i * (CW + CG) - CW / 2);
        }));
        return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;
      };
      const upWant = (u) => {
        const xs = [];
        u.ids.forEach((id) => (partnerFams[id] || []).forEach((f) => f.children.forEach((c) => {
          if (unitOf[c.id]) xs.push(cardX(c.id) + CW / 2);
        })));
        if (!xs.length) return null;
        const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
        return mean - u.w / 2;
      };
      const place = (g, want, reorder) => {
        const row = order[g];
        const d = row.map((u) => { const w = want(u); return w === null ? u.x : w; });
        if (reorder) {
          const idx = row.map((u, i) => i).sort((a, b) => d[a] - d[b] || a - b);
          order[g] = idx.map((i) => row[i]);
          const dd = idx.map((i) => d[i]);
          const xs = pav(dd, order[g].map((u) => u.w), GAP);
          order[g].forEach((u, i) => { u.x = xs[i]; });
        } else {
          const xs = pav(d, row.map((u) => u.w), GAP);
          row.forEach((u, i) => { u.x = xs[i]; });
        }
      };
      // each unit wants to sit under its parents and over its children; where it
      // has both, halfway. Sweep down and up, re-sorting rows at first.
      const both = (u) => { const a = downWant(u), b = upWant(u); return a === null ? b : b === null ? a : (a + b) / 2; };
      for (let it = 0; it < 12; it++) {
        const reorder = it < 8;
        gens.forEach((g) => place(g, both, reorder));
        [...gens].reverse().forEach((g) => place(g, both, reorder));
      }
      // ---- shift the group to the right of the previous one ----
      let minX = Infinity, maxX = -Infinity;
      units.forEach((u) => { minX = Math.min(minX, u.x); maxX = Math.max(maxX, u.x + u.w); });
      const shift = offsetX - minX;
      units.forEach((u) => { u.x += shift; });
      offsetX += (maxX - minX) + CW;
      // ---- cards ----
      units.forEach((u) => u.ids.forEach((id, i) => out.cards.push({ p: P[id], x: u.x + i * (CW + CG), y: u.gen * ROW })));
      // ---- lines ----
      const barsPerRow = {};
      members.forEach((p) => (partnerFams[p.id] || []).forEach((f) => {
        if (f._drawn) return;
        f._drawn = true;
        const a = f.p1 && unitOf[f.p1], b = f.p2 && unitOf[f.p2];
        let ax, ay;
        if (a && b && P[f.p1].gen === P[f.p2].gen) {
          const x1 = cardX(f.p1), x2 = cardX(f.p2);
          const l = Math.min(x1, x2) + CW, r = Math.max(x1, x2);
          ay = P[f.p1].gen * ROW + CH / 2;
          out.lines.push({ d: `M${l},${ay} H${r}`, cls: f.ended ? "couple ended" : "couple" });
          ax = (l + r) / 2;
        } else {
          const only = a ? f.p1 : f.p2;
          ax = cardX(only) + CW / 2;
          ay = P[only].gen * ROW + CH;
        }
        const kids = f.children.filter((c) => unitOf[c.id]);
        if (!kids.length) return;
        const kg = P[kids[0].id].gen;
        const n = barsPerRow[kg] = (barsPerRow[kg] || 0) + 1;
        const barY = kg * ROW - VG / 2 + ((n % 5) - 2) * 5;
        const tops = kids.map((c) => cardX(c.id) + CW / 2);
        out.lines.push({ d: `M${ax},${ay} V${barY}`, cls: "desc" });
        out.lines.push({ d: `M${Math.min(ax, ...tops)},${barY} H${Math.max(ax, ...tops)}`, cls: "desc" });
        kids.forEach((c, i) => out.lines.push({ d: `M${tops[i]},${barY} V${P[c.id].gen * ROW}`, cls: c.relation !== "birth" ? "desc nonbirth" : "desc" }));
      }));
    }
    return out;
  }

  function shiftLine(l, dx) {
    return Object.assign({}, l, { d: l.d.replace(/M(-?[\d.]+),/g, (m, a) => `M${+a + dx},`).replace(/H(-?[\d.]+)/g, (m, a) => `H${+a + dx}`) });
  }

  // ---------- drawing ----------
  function drawCard(c, data, handlers) {
    const g = s("g", { class: "card-g", transform: `translate(${c.x},${c.y})`, tabindex: "0", role: "button" });
    if (c.ghost) {
      const label = { father: "+ Add father", mother: "+ Add mother", parent: "+ Add parent", partner: "+ Add partner", child: "+ Add child" }[c.ghost];
      g.setAttribute("class", "card-g ghost");
      g.setAttribute("aria-label", label);
      g.appendChild(s("rect", { width: CW, height: CH, rx: 12, class: "ghost-rect" }));
      g.appendChild(s("text", { x: CW / 2, y: CH / 2 + 5, "text-anchor": "middle", class: "ghost-text" }, label));
      g.addEventListener("click", (e) => { e.stopPropagation(); handlers.onGhost && handlers.onGhost(c); });
      g.addEventListener("keydown", (e) => { if (e.key === "Enter") handlers.onGhost && handlers.onGhost(c); });
      return g;
    }
    const p = c.p;
    const isFocus = p.id === data.focus, isMe = p.id === data.meId;
    const extra = handlers.cardClass ? handlers.cardClass(p) : "";
    const base = "card-g" + (isFocus ? " focus" : "") + (p.living ? "" : " deceased") + (c.sibling ? " sibling" : "");
    g.setAttribute("class", base + (extra ? " " + extra : ""));
    g.dataset.base = base;
    g.dataset.id = p.id;
    const rel = handlers.relLabel ? handlers.relLabel(p)
      : (p.relationship && p.relationship !== "you" && p.relationship !== "not related" ? ` — your ${p.relationship}` : "");
    const title = p.name + (p.years ? ` (${p.years})` : "") + rel;
    g.setAttribute("aria-label", title);
    g.appendChild(s("title", null, title));
    g.appendChild(s("rect", { width: CW, height: CH, rx: 12, class: "card-rect" }));
    g.appendChild(s("rect", { width: 5, height: CH - 16, x: 0, y: 8, rx: 2, class: `sex-bar sex-${p.gender}` }));
    const cx = 32, cy = CH / 2, r = 21;
    if (p.photo) {
      const clipId = "clip-" + p.id + "-" + Math.round(c.x) + "-" + Math.round(c.y);
      g.appendChild(s("clipPath", { id: clipId }, s("circle", { cx, cy, r })));
      g.appendChild(s("circle", { cx, cy, r: r + 1.5, class: "avatar-ring" }));
      const href = handlers.photoUrl ? handlers.photoUrl(p)
        : `api/media/${encodeURIComponent(p.photo)}/file?size=256${p.photoRegion ? "&region=" + encodeURIComponent(p.photoRegion) : ""}`;
      g.appendChild(s("image", { href, x: cx - r, y: cy - r,
        width: r * 2, height: r * 2, preserveAspectRatio: "xMidYMid slice", "clip-path": `url(#${clipId})` }));
    } else {
      g.appendChild(s("circle", { cx, cy, r, class: `avatar sex-${p.gender}` }));
      g.appendChild(s("text", { x: cx, y: cy + 5, "text-anchor": "middle", class: "avatar-text" }, initials(p)));
    }
    const tx = 60;
    // names in script (§13.7): window.__nameDisplay is "en" (default), "script" or "both"
    const nd = window.__nameDisplay || "en";
    const useLocal = nd === "script" && (p.givenLocal || p.surnameLocal);
    const gv = useLocal ? p.givenLocal : p.given, sn = useLocal ? p.surnameLocal : p.surname;
    const given = gv || (sn ? "" : p.name);
    g.appendChild(s("text", { x: tx, y: 24, class: "name" }, fit(given || p.name, 14)));
    const second = nd === "both" && p.nameLocal ? p.nameLocal : (sn || "");
    g.appendChild(s("text", { x: tx, y: 40, class: "surname" + (nd === "both" && p.nameLocal ? " local" : "") }, fit(second, 15)));
    const yr = (p.years || "") + (p.living ? "" : " 🕯");
    g.appendChild(s("text", { x: tx, y: 56, class: "years" }, fit(yr.trim(), 18)));
    if (isMe) g.appendChild(s("text", { x: CW - 8, y: 15, "text-anchor": "end", class: "me-badge" }, "Me"));
    const badge = handlers.badge ? handlers.badge(p) : null;
    if (badge) g.appendChild(s("text", { x: CW - 8, y: CH - 8, "text-anchor": "end", class: "cut-badge" }, badge));
    if (c.sibling && p.full === false) g.appendChild(s("text", { x: CW - 8, y: 15, "text-anchor": "end", class: "half-badge" }, "half"));
    if (c.more) g.appendChild(s("text", { x: CW - 8, y: CH - 7, "text-anchor": "end", class: "more-badge" }, `+${c.more}`));
    if (c.truncatedUp) g.appendChild(s("text", { x: CW / 2, y: -6, "text-anchor": "middle", class: "more-badge" }, "▲"));
    return g;
  }

  function render(container, data, handlers) {
    while (container.firstChild) container.removeChild(container.firstChild);
    const lay = handlers.whole ? layoutAll(data) : layout(data, { editable: !!handlers.editable });
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const c of lay.cards) {
      minX = Math.min(minX, c.x); minY = Math.min(minY, c.y);
      maxX = Math.max(maxX, c.x + CW); maxY = Math.max(maxY, c.y + CH);
    }
    const svg = s("svg", { class: "tree-svg", width: "100%", height: "100%", role: "img", "aria-label": "Family tree chart" });
    const vp = s("g", { class: "viewport" });
    const linesG = s("g", { class: "lines" });
    for (const l of lay.lines) linesG.appendChild(s("path", { d: l.d, class: "line " + l.cls }));
    const cardsG = s("g", { class: "cards" });
    const byId = {};
    for (const c of lay.cards) {
      const el = drawCard(c, data, handlers);
      cardsG.appendChild(el);
      if (c.p && c.p.id === data.focus && !c.sibling) byId.focus = c;
      if (c.p) byId[c.p.id] = byId[c.p.id] || c;
    }
    vp.appendChild(linesG);
    vp.appendChild(cardsG);
    // stripes for people on both sides (father's and mother's, §13.16)
    svg.appendChild(s("defs", null, s("pattern", { id: "side-stripes", width: 10, height: 10, patternUnits: "userSpaceOnUse",
      patternTransform: "rotate(45)" }, s("rect", { width: 5, height: 10, class: "stripe-a" }), s("rect", { x: 5, width: 5, height: 10, class: "stripe-b" }))));
    svg.appendChild(vp);
    container.appendChild(svg);

    // ---- pan & zoom ----
    const view = { k: 1, x: 0, y: 0 };
    const apply = () => vp.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
    const rect = () => svg.getBoundingClientRect();
    function centerOn(c, k) {
      const r = rect();
      view.k = k || view.k;
      view.x = r.width / 2 - (c.x + CW / 2) * view.k;
      view.y = r.height / 2 - (c.y + CH / 2) * view.k;
      apply();
    }
    function fitAll() {
      const r = rect();
      const w = maxX - minX + 80, hgt = maxY - minY + 80;
      view.k = Math.max(handlers.whole ? 0.04 : 0.2, Math.min(1.2, r.width / w, r.height / hgt));
      view.x = (r.width - (maxX + minX) * view.k) / 2;
      view.y = (r.height - (maxY + minY) * view.k) / 2;
      apply();
    }
    function zoomAt(factor, px, py) {
      const k = Math.max(handlers.whole ? 0.04 : 0.15, Math.min(3, view.k * factor));
      const f = k / view.k;
      view.x = px - (px - view.x) * f;
      view.y = py - (py - view.y) * f;
      view.k = k;
      apply();
    }
    if (byId.focus && !handlers.fitFirst) centerOn(byId.focus, handlers.initialScale || (handlers.whole ? 0.8 : 1)); else fitAll();

    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = rect();
      zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    const pointers = new Map();
    let drag = null, pinch = null, moved = false;
    let lastTap = { id: null, t: 0 }, tapTimer = null;
    svg.addEventListener("pointerdown", (e) => {
      if (e.target.closest && e.target.closest(".ghost")) return;
      svg.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      moved = false;
      if (pointers.size === 1) drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), k: view.k };
        drag = null;
      }
    });
    svg.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y);
        const r = rect();
        zoomAt((pinch.k * d / pinch.d) / view.k, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
        moved = true;
      } else if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        view.x = drag.vx + dx; view.y = drag.vy + dy;
        apply();
      }
    });
    const end = (e) => {
      const wasTap = !moved && pointers.size === 1;
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinch = null;
      if (pointers.size === 0) drag = null;
      if (!wasTap || e.type === "pointercancel") return;
      const target = document.elementFromPoint(e.clientX, e.clientY);
      const cardEl = target && target.closest ? target.closest(".card-g") : null;
      if (!cardEl || !cardEl.dataset.id) return;
      const id = cardEl.dataset.id;
      const now = Date.now();
      if (lastTap.id === id && now - lastTap.t < 320) {
        clearTimeout(tapTimer);
        lastTap = { id: null, t: 0 };
        handlers.onRecenter && handlers.onRecenter(id);
      } else {
        lastTap = { id, t: now };
        clearTimeout(tapTimer);
        tapTimer = setTimeout(() => handlers.onOpen && handlers.onOpen(id), 320);
      }
    };
    svg.addEventListener("pointerup", end);
    svg.addEventListener("pointercancel", end);
    svg.addEventListener("keydown", (e) => {
      const cardEl = e.target.closest && e.target.closest(".card-g");
      if (cardEl && cardEl.dataset.id && e.key === "Enter") handlers.onOpen && handlers.onOpen(cardEl.dataset.id);
    });

    // re-apply cardClass/badge after the caller's state changed, without a new layout
    function restyle() {
      const people = {};
      for (const c of lay.cards) if (c.p) people[c.p.id] = c.p;
      cardsG.querySelectorAll(".card-g[data-id]").forEach((g) => {
        const p = people[g.dataset.id];
        if (!p) return;
        const extra = handlers.cardClass ? handlers.cardClass(p) : "";
        g.setAttribute("class", g.dataset.base + (extra ? " " + extra : ""));
        g.querySelectorAll(".cut-badge").forEach((b) => b.remove());
        const badge = handlers.badge ? handlers.badge(p) : null;
        if (badge) g.appendChild(s("text", { x: CW - 8, y: CH - 8, "text-anchor": "end", class: "cut-badge" }, badge));
      });
    }

    return {
      restyle,
      zoomIn: () => { const r = rect(); zoomAt(1.2, r.width / 2, r.height / 2); },
      zoomOut: () => { const r = rect(); zoomAt(1 / 1.2, r.width / 2, r.height / 2); },
      fit: fitAll,
      center: () => byId.focus && centerOn(byId.focus, 1),
      centerOnId: (id, k) => { if (byId[id]) { centerOn(byId[id], k || Math.max(view.k, 0.8)); return true; } return false; },
    };
  }

  window.FamilyChart = { render, CARD_W: CW, CARD_H: CH };
})();
