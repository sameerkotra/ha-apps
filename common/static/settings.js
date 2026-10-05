/* settings.js — Admin → App settings, drawn from the server's description of each setting
 * (shared: common/static/settings.js; the server side is common/python/settings_core.py).
 *
 * Needs common/ui.js (window.UI) and common/settings.css. One global, window.SettingsPage:
 *
 *   SettingsPage.render(box, opts) → Promise<page>     draws the page into `box`
 *
 * The page comes from GET /api/admin/settings: {values, defaults, meta, groups, secretsSet?, …}. Each group is a
 * card (title, help) with its settings in `meta` order; each setting is drawn by its kind — "bool" (a switch),
 * "int" / "float" (a number box: whole numbers, min–max), "choice" (a drop-down of meta.choices), "text" / "url",
 * "secret" (write-only: "saved — type to replace", and a Remove button), "list" with choices (tick boxes) —
 * with its label, help, range, unit, default, and its own error line. meta.showIf hides a setting while that
 * switch is off; meta.enabledIf greys it out instead; meta.hidden leaves it off this page; meta.suggestions adds
 * a pick-list to a text box; meta.restartRequired marks it "restart needed". Changes are kept until Save, which
 * sends only what changed (PUT, the same answer as GET); Discard puts everything back. Wrong values are
 * caught before saving (per field) and the server's own message is shown too.
 *
 * opts (all optional except load/save):
 *   load()                    → Promise<payload>       (GET)
 *   save(body)                → Promise<payload>       (PUT; body = the changed settings, plus ctx.extra)
 *   data                      a payload already loaded
 *   classes                   { card, primary, secondary, ghost, input?, select? } — the app's own class names
 *   intro, footer             a node / text / (page) => node, above the groups / under the save bar
 *   fields[key]               { control(page, f) → element (the input; call page.set(key, v)),
 *                               after(page, f) → node (under the help), render(page, f) → whole row,
 *                               hidden(page) → bool }
 *   groups[id]                { top(page) / bottom(page) → node(s), hidden(page) → bool, title }
 *   beforeSave(body, page)    → Promise<body | null> (null: don't save — e.g. a confirmation was cancelled)
 *   afterSave(data, body, page) → Promise<message | undefined>
 *   onChange(key, value, page)  after every edit
 *   afterDraw(page)           after the page is (re)drawn (on load, after Save / Discard)
 *   toast(message, isError)   the app's toast (also shown in the save bar's status line)
 *   savedMessage, saveLabel, discardLabel, jump (true: links to the groups; default when there are 5+)
 *
 * page: { data, values (saved), edits, extra (sent with the next save, e.g. {confirm: true}), value(key),
 *         set(key, value), unset(key), setError(key, message|null), error(key), dirty(), status(text, kind), refresh(data),
 *         reload(), save(), row(key), box }
 */
(function () {
  "use strict";
  const { h, mount, clear } = window.UI;

  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const asNodes = (x, page) => (typeof x === "function" ? x(page) : x);
  let leaveGuard = null;

  function defaultText(m, d) {
    if (d === undefined || d === null || m.kind === "secret" || m.kind === "list") return "";
    if (m.kind === "bool") return `Default: ${d ? "on" : "off"}.`;
    if (m.choices) {
      const c = m.choices.find((x) => same(x.value, d));
      return c ? `Default: ${c.label}.` : "";
    }
    if (d === "") return "";
    return `Default: ${d}${m.unit && typeof d === "number" ? " " + m.unit : ""}.`;
  }

  function rangeText(m) {
    if (m.kind !== "int" && m.kind !== "float") return "";
    if (m.min === undefined || m.max === undefined) return "";
    return `${m.min}–${m.max}${m.unit ? " " + m.unit : ""}.`;
  }

  function numberProblem(m, raw) {
    const whole = m.kind === "int";
    const what = whole ? "a whole number" : "a number";
    const range = m.min !== undefined && m.max !== undefined ? ` from ${m.min} to ${m.max}` : "";
    const msg = `${m.label} must be ${what}${range}.`;
    if (String(raw).trim() === "") return msg;
    const n = Number(raw);
    if (!Number.isFinite(n) || (whole && !Number.isInteger(n))) return msg;
    if ((m.min !== undefined && n < m.min) || (m.max !== undefined && n > m.max)) return msg;
    return null;
  }

  async function render(box, opts) {
    const o = Object.assign({ classes: {}, fields: {}, groups: {} }, opts || {});
    const cls = Object.assign({ card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" }, o.classes);
    const page = { box, data: null, values: null, edits: {}, extra: {}, errors: {}, rows: {}, opts: o };
    let statusEl, noteEl, saveBtn, discardBtn, bar, saving = false;

    page.value = (k) => (k in page.edits ? page.edits[k] : page.values[k]);
    page.meta = (k) => page.data.meta[k];
    page.error = (k) => page.errors[k] || null;
    page.row = (k) => page.rows[k] || null;
    page.dirty = () => Object.keys(page.edits).length > 0 || Object.keys(page.extra).some((k) => page.extra[k] !== undefined && k.startsWith("clear_"));

    page.status = (text, kind) => {
      if (!statusEl) return;
      statusEl.textContent = text || "";
      statusEl.className = "sp-status" + (kind ? " " + kind : "");
    };

    page.setError = (k, msg) => {
      if (msg) page.errors[k] = msg; else delete page.errors[k];
      const row = page.rows[k];
      if (row) {
        const line = row.querySelector(":scope .sp-error");
        if (line) line.textContent = msg || "";
        row.classList.toggle("sp-invalid", !!msg);
      }
      paintBar();
    };

    page.set = (k, v, opts2) => {
      const keepRaw = opts2 && opts2.invalid;
      if (!keepRaw && same(v, page.values[k]) && page.meta(k).kind !== "secret") delete page.edits[k];
      else page.edits[k] = v;
      if (!keepRaw) page.setError(k, null);
      page.status("");
      paintChanged();
      applyConditions();
      if (o.onChange) o.onChange(k, v, page);
      paintBar();
    };

    page.unset = (k) => {
      delete page.edits[k];
      page.setError(k, null);
      paintChanged();
      applyConditions();
      paintBar();
    };

    function paintChanged() {
      for (const [k, row] of Object.entries(page.rows)) {
        const tag = row.querySelector(":scope .sp-changed");
        if (tag) tag.hidden = !(k in page.edits);
      }
    }

    function paintBar() {
      if (!saveBtn) return;
      const n = Object.keys(page.edits).length + Object.keys(page.extra).filter((k) => k.startsWith("clear_") && page.extra[k]).length;
      saveBtn.disabled = saving || n === 0;
      bar.classList.toggle("sp-dirty", n > 0);
      discardBtn.disabled = saving || n === 0;
      noteEl.textContent = n ? `${n} unsaved change${n === 1 ? "" : "s"}` : "";
    }

    function applyConditions() {
      for (const [k, row] of Object.entries(page.rows)) {
        const m = page.meta(k);
        const f = o.fields[k] || {};
        let hide = !!(m.showIf && !page.value(m.showIf));
        if (f.hidden && f.hidden(page)) hide = true;
        row.hidden = hide;
        if (m.enabledIf) {
          const on = !!page.value(m.enabledIf);
          row.classList.toggle("sp-disabled", !on);
          row.querySelectorAll("input, select, textarea, button").forEach((el) => { el.disabled = !on; });
          const note = row.querySelector(":scope .sp-off-note");
          if (note) note.hidden = on;
        }
      }
      for (const sec of box.querySelectorAll(".sp-group")) {
        const g = o.groups[sec.dataset.group] || {};
        const rows = [...sec.querySelectorAll(":scope .sp-field")];
        const hasExtras = !!sec.querySelector(":scope .sp-extra");
        let hide = rows.length > 0 && rows.every((r) => r.hidden) && !hasExtras;
        if (g.hidden && g.hidden(page)) hide = true;
        sec.hidden = hide;
        const link = box.querySelector(`.sp-jump a[href="#${sec.id}"]`);
        if (link) link.hidden = hide;
      }
    }

    // ---------- one control per kind ----------
    function control(k, m, id) {
      const cur = page.value(k);
      if (m.kind === "bool") {
        const input = h("input", { type: "checkbox", id, role: "switch", checked: !!cur, "aria-describedby": `${id}-help` });
        input.addEventListener("change", () => page.set(k, input.checked));
        return h("label", { class: "sp-switch", title: m.label }, input, h("span", { class: "sp-track" }, h("span", { class: "sp-thumb" })));
      }
      if (m.kind === "choice") {
        const sel = h("select", { id, "aria-describedby": `${id}-help` },
          m.choices.map((c, i) => h("option", { value: String(i) }, c.label)));
        const at = m.choices.findIndex((c) => same(c.value, cur));
        sel.value = String(at < 0 ? 0 : at);
        if (at < 0) sel.appendChild(h("option", { value: "-1" }, String(cur)));
        if (at < 0) sel.value = "-1";
        sel.addEventListener("change", () => { const c = m.choices[Number(sel.value)]; if (c) page.set(k, c.value); });
        return sel;
      }
      if (m.kind === "int" || m.kind === "float") {
        const input = h("input", { type: "number", id, inputmode: m.kind === "int" ? "numeric" : "decimal",
          min: m.min, max: m.max, step: m.step !== undefined ? String(m.step) : (m.kind === "int" ? "1" : "any"),
          value: String(cur), class: "sp-num", "aria-describedby": `${id}-help` });
        input.addEventListener("input", () => {
          const problem = numberProblem(m, input.value);
          if (problem) { page.set(k, input.value, { invalid: true }); page.setError(k, problem); }
          else page.set(k, Number(input.value));
        });
        return input;
      }
      if (m.kind === "secret") {
        const saved = !!(page.data.secretsSet && page.data.secretsSet[k]);
        const hint = page.data.secrets && page.data.secrets[k] && page.data.secrets[k].hint;
        const input = h("input", { type: "password", id, autocomplete: "new-password", spellcheck: "false",
          maxlength: m.maxLength, "aria-describedby": `${id}-help`,
          placeholder: saved ? `Saved${hint ? ` (${hint})` : ""} — type a new one to replace it` : (m.placeholder || "Not set") });
        input.addEventListener("input", () => {
          if (m.clearFlag) delete page.extra[m.clearFlag];
          if (input.value) page.set(k, input.value); else page.unset(k);
        });
        const remove = saved ? h("button", { type: "button", class: cls.ghost, onclick: () => {
          input.value = "";
          input.placeholder = "Will be removed when you save";
          if (m.clearFlag) { page.extra[m.clearFlag] = true; delete page.edits[k]; paintBar(); paintChanged(); page.status(""); }
          else page.set(k, "");
          const tag = page.rows[k] && page.rows[k].querySelector(":scope .sp-changed");
          if (tag) tag.hidden = false;
        } }, "Remove") : null;
        return h("div", { class: "sp-secret-box" }, input, remove);
      }
      if (m.kind === "list" && m.choices) {
        const wrap = h("div", { class: "sp-checks", role: "group", "aria-label": m.label, id });
        m.choices.forEach((c) => {
          const box_ = h("input", { type: "checkbox", checked: (cur || []).some((x) => same(x, c.value)) });
          box_.addEventListener("change", () => {
            const on = new Set(m.choices.filter((_, i) => wrap.querySelectorAll("input")[i].checked).map((x) => JSON.stringify(x.value)));
            page.set(k, m.choices.map((x) => x.value).filter((v) => on.has(JSON.stringify(v))));
          });
          wrap.appendChild(h("label", null, box_, c.label));
        });
        return wrap;
      }
      // text / url
      const listId = m.suggestions ? `${id}-list` : null;
      const input = h("input", { type: m.kind === "url" ? "url" : "text", id, value: cur === null || cur === undefined ? "" : String(cur),
        maxlength: m.maxLength, placeholder: m.placeholder, spellcheck: "false", autocomplete: "off", list: listId,
        class: m.kind === "url" || (m.maxLength || 0) > 60 ? "sp-wide" : (m.maxLength && m.maxLength <= 8 ? "sp-short" : null), "aria-describedby": `${id}-help` });
      input.addEventListener("input", () => page.set(k, input.value.trim()));
      if (!listId) return input;
      return h("span", { class: "sp-inline" }, input, h("datalist", { id: listId }, m.suggestions.map((s) => h("option", { value: s }))));
    }

    function fieldRow(k, m) {
      const f = o.fields[k] || {};
      if (f.render) {
        const row = h("div", { class: "sp-field sp-custom", dataset: { key: k } }, f.render(page, f));
        page.rows[k] = row;
        return row;
      }
      const id = `set-${k}`;
      const ctrl = f.control ? f.control(page, f) : control(k, m, id);
      const help = [m.help, rangeText(m), defaultText(m, page.data.defaults[k])].filter(Boolean).join(" ");
      const label = h("span", { class: "sp-label" }, m.label,
        m.restartRequired ? h("span", { class: "sp-restart", title: "Takes effect after the app restarts" }, "restart needed") : null,
        h("span", { class: "sp-changed", hidden: true }, "changed"));
      const helpEl = h("div", { class: "sp-help", id: `${id}-help` }, help,
        m.enabledIf ? h("span", { class: "sp-off-note", hidden: true }, ` Only used while “${page.meta(m.enabledIf).label}” is on.`) : null);
      const after = f.after ? f.after(page, f) : null;
      const err = h("div", { class: "sp-error", role: "alert" });
      let row;
      if (m.kind === "bool" && !f.control) {
        row = h("div", { class: "sp-field sp-bool sp-kind-bool", dataset: { key: k } },
          h("div", { class: "sp-text" }, h("label", { for: id }, label), helpEl, after), ctrl, err);
      } else {
        row = h("div", { class: `sp-field sp-kind-${m.kind}`, dataset: { key: k } },
          h("label", { for: id }, label), ctrl, helpEl, after, err);
      }
      page.rows[k] = row;
      return row;
    }

    function draw() {
      page.rows = {};
      page.errors = {};
      const meta = page.data.meta;
      const groups = (page.data.groups || []).slice();
      const known = new Set(groups.map((g) => g.id));
      for (const m of Object.values(meta)) {
        if (!known.has(m.group || "")) { known.add(m.group || ""); groups.push({ id: m.group || "", label: "App settings", help: "" }); }
      }
      const sections = groups.map((g) => {
        const gx = o.groups[g.id] || {};
        const keys = Object.keys(meta).filter((k) => (meta[k].group || "") === g.id && !meta[k].hidden);
        const top = gx.top ? asNodes(gx.top, page) : null;
        const bottom = gx.bottom ? asNodes(gx.bottom, page) : null;
        if (!keys.length && !top && !bottom) return null;
        return h("section", { class: `${cls.card} sp-group`.trim(), id: `sg-${g.id || "general"}`, dataset: { group: g.id } },
          h("h3", { class: "sp-title" }, gx.title || g.label),
          g.help ? h("p", { class: "sp-group-help" }, g.help) : null,
          top ? h("div", { class: "sp-extra" }, top) : null,
          keys.map((k) => fieldRow(k, meta[k])),
          bottom ? h("div", { class: "sp-extra" }, bottom) : null);
      }).filter(Boolean);
      const showJump = o.jump !== undefined ? o.jump : sections.length >= 5;
      const jump = showJump ? h("nav", { class: "sp-jump", "aria-label": "Setting groups" }, sections.map((s) =>
        h("a", { href: `#${s.id}`, onclick: (e) => { e.preventDefault(); s.scrollIntoView({ behavior: "smooth", block: "start" }); } },
          s.querySelector(".sp-title").textContent))) : null;
      statusEl = h("span", { class: "sp-status", role: "status", "aria-live": "polite" });
      noteEl = h("span", { class: "sp-note" });
      saveBtn = h("button", { type: "button", class: cls.primary, id: "settingsSave", onclick: () => page.save() }, o.saveLabel || "Save");
      discardBtn = h("button", { type: "button", class: cls.ghost, id: "settingsDiscard", onclick: () => { page.edits = {}; page.extra = {}; draw(); } },
        o.discardLabel || "Discard changes");
      mount(box, h("div", { class: "sp-page" },
        o.intro ? h("div", { class: "sp-intro" }, asNodes(o.intro, page)) : null,
        jump, sections,
        bar = h("div", { class: "sp-bar" }, h("div", { class: "sp-bar-text" }, noteEl, statusEl), discardBtn, saveBtn),
        o.footer ? h("div", { class: "sp-footer" }, asNodes(o.footer, page)) : null));
      if (cls.input) box.querySelectorAll(".sp-field input[type=text], .sp-field input[type=url], .sp-field input[type=password], .sp-field input[type=number]")
        .forEach((el) => el.classList.add(...cls.input.split(" ")));
      if (cls.select) box.querySelectorAll(".sp-field select").forEach((el) => el.classList.add(...cls.select.split(" ")));
      paintChanged();
      applyConditions();
      paintBar();
      if (o.afterDraw) o.afterDraw(page);
    }

    page.refresh = (data) => {
      page.data = data;
      page.values = data.values;
      page.edits = {};
      page.extra = {};
      draw();
    };

    page.reload = async () => { page.refresh(await o.load()); };

    function serverErrorKey(msg) {
      for (const [k, m] of Object.entries(page.data.meta)) {
        if (msg.startsWith(m.label + ":") || msg.startsWith(m.label + " ") || msg.startsWith(k + ":") || msg.startsWith(k + ".")) return k;
      }
      return null;
    }

    page.save = async () => {
      const bad = Object.keys(page.errors).filter((k) => page.rows[k] && !page.rows[k].hidden);
      if (bad.length) {
        page.status(page.errors[bad[0]], "err");
        const el = page.rows[bad[0]].querySelector("input, select, textarea");
        if (el) el.focus();
        return;
      }
      let body = Object.assign({}, page.edits);
      for (const [k, v] of Object.entries(page.extra)) if (v !== undefined) body[k] = v;
      if (o.beforeSave) {
        try { body = await o.beforeSave(body, page); }
        catch (e) { page.status(e.message, "err"); return; }
        if (!body) return;
      }
      if (!Object.keys(body).length) { page.status("Nothing to save."); return; }
      saving = true; paintBar(); page.status("Saving…");
      try {
        const before = page.values;
        const data = await o.save(body);
        const restart = Object.keys(body).filter((k) => page.data.meta[k] && page.data.meta[k].restartRequired && !same(before[k], data.values[k]));
        page.refresh(data);
        let msg = o.afterSave ? await o.afterSave(data, body, page) : undefined;
        if (msg === undefined) msg = o.savedMessage || "Saved — changes apply straight away.";
        if (restart.length) msg += ` Restart the app for ${restart.map((k) => page.data.meta[k].label).join(", ")} to take effect.`;
        if (msg) { page.status(msg, "ok"); if (o.toast) o.toast(msg); }
      } catch (e) {
        const msg = (e && e.message) || "Not saved.";
        page.status(msg, "err");
        const k = serverErrorKey(msg);
        if (k && page.rows[k]) page.setError(k, msg);
      } finally {
        saving = false; paintBar();
      }
    };

    if (leaveGuard) window.removeEventListener("beforeunload", leaveGuard);
    leaveGuard = (e) => { if (document.body.contains(box) && page.dirty()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", leaveGuard);

    page.refresh(o.data || await o.load());
    return page;
  }

  window.SettingsPage = { render, numberProblem, defaultText };
})();
