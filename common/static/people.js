/* people.js — Admin → People, the parts every household app shares (shared: common/static/people.js; the server
 * side is common/python/people_admin.py). Needs common/ui.js (window.UI); styled by common/settings.css (.pp-*).
 *
 * One global, window.PeoplePage:
 *
 *   PeoplePage.render(box, opts)          the list: an intro, "Check Home Assistant again", then one card per person
 *     opts.people                         [{id, name, …}]
 *     opts.intro                          node(s) / text above the list
 *     opts.checkAgain                     async () => void — the "Check Home Assistant again" link (… then redraw)
 *     opts.person(p)                      → { avatar: node, badges: [[text, kind]], sub: text|node, controls: node(s) (right of the
 *                                           name: access switch, …), actions: node(s), blocks: node(s) (under the
 *                                           head: the app's own parts) } — every app's own columns and actions
 *     opts.notify                         the notify editor's options (below), shown in each card (mode "inline")
 *                                         or behind a 🔔 button that opens a dialog (mode "dialog"); null: none
 *     opts.empty                          text when there's nobody
 *     opts.cardClass                      the app's card class ("card")
 *   PeoplePage.notifyEditor(p, o)         Phones (from Home Assistant, read-only), Also (extra notify services, each
 *                                         removable), Add (a pick-list of what HA offers, or a typed name), Send a test
 *     o.api(path, opts)                   the app's api()
 *     o.path(p)                           "api/admin/users/<id>/notify" (POST adds, DELETE …/<service> removes)
 *     o.testPath(p)                       the test route
 *     o.services                          GET …/notify-services answer ({available, error, services, entities})
 *     o.usable(p)                         how many of their phones count (default: those with a notify action)
 *     o.onChange(p, answer)               after an add/remove (answer: the route's JSON); default: p.notify = answer.notify
 *     o.toast(msg, isError) / o.fail(err) the app's toasts
 *     o.texts                             { intro(p), none(p, reachable), phonesHelp } — the app's wording
 *     o.extraLine(p)                      a node on the Phones line (e.g. "reminders on")
 *     o.retry()                           "Try again" next to "couldn't list the notify services" (the list page)
 *   PeoplePage.notifyDialog(p, o)         the editor in o.openModal("Notifications — <name>", …, {onClose: o.onClose});
 *                                         o.loadServices() → the notify-services answer, fetched when it opens
 *   PeoplePage.phoneChips(p, short)       the Phones chips alone (e.g. for a table cell)
 *   PeoplePage.accessSwitch(on, onChange, {label, disabled})  a switch (the same as App settings')
 */
(function () {
  "use strict";
  const { h, mount } = window.UI;
  const NOTIFY_RE = /^notify\.[a-z0-9_]+$/;

  function accessSwitch(on, onChange, opts) {
    const o = opts || {};
    const input = h("input", { type: "checkbox", role: "switch", checked: !!on, disabled: !!o.disabled, "aria-label": o.label || "Access" });
    input.addEventListener("change", () => onChange(input.checked, input));
    return h("label", { class: "sp-switch", title: o.label || null }, input, h("span", { class: "sp-track" }, h("span", { class: "sp-thumb" })));
  }

  // The person's phones from Home Assistant (Settings → People → Track device) — read-only here.
  function phoneChips(p, short) {
    const ha = p.ha || { known: false, phones: [] };
    if (!ha.known) return [h("span", { class: "pp-hint" }, short ? "Not read yet" : "Home Assistant's people couldn't be read yet.")];
    if (!ha.person) {
      return [h("span", { class: "pp-hint", title: "Settings → People → Allow person to login" },
        short ? "No person linked" : "No Home Assistant person is linked to this login (Settings → People → the person → Allow person to login).")];
    }
    if (!ha.phones.length) {
      return [h("span", { class: "pp-hint", title: `Settings → People → ${ha.personName} → Track device` },
        short ? "No phone" : `${ha.personName} has no phone in Home Assistant — Settings → People → ${ha.personName} → Track device.`)];
    }
    return ha.phones.map((ph) => (ph.service
      ? h("span", { class: "pp-chip phone", title: `${ph.tracker || ""} → ${ph.service}` }, "📱 " + ph.label,
        short ? null : h("span", { class: "pp-chip-hint" }, ph.service))
      : h("span", { class: "pp-chip warn", title: `${ph.tracker || ""}: Home Assistant has no notify action for this phone` },
        `⚠ ${ph.label}`, short ? null : h("span", { class: "pp-chip-hint" }, "— Companion app action not found"))));
  }

  const linkedPhones = (p) => ((p.ha && p.ha.phones) || []).filter((ph) => ph.service).length;

  function resultsText(results) {
    const list = Array.isArray(results) ? results
      : Object.entries(results || {}).map(([service, ok]) => ({ service, ok, hint: "" }));
    return { list, text: list.map((y) => `${y.service}: ${y.ok ? "sent ✓" : "failed" + (y.hint ? " — " + y.hint : "")}`).join(" · ") };
  }

  function notifyEditor(p, o) {
    const box = h("div", { class: "pp-notify", dataset: { userId: p.id } });
    const texts = o.texts || {};
    const toast = o.toast || ((m) => window.UI.toast(m));
    const fail = o.fail || ((e) => toast(e.message || String(e), true));
    const usable = () => (o.usable ? o.usable(p) : linkedPhones(p));
    const changed = (answer) => { if (o.onChange) o.onChange(p, answer); else if (answer && answer.notify) p.notify = answer.notify; };
    const draw = () => {
      const avail = o.services || { available: false, services: [], entities: [], error: null };
      const have = new Set(p.notify || []);
      const offered = (list) => (list || []).filter((s) => !have.has(s) && s !== "notify.persistent_notification");
      const services = offered(avail.services), entities = offered(avail.entities);
      const manual = h("input", { type: "text", placeholder: "notify.mobile_app_phone", "aria-label": `Notify service for ${p.name}`,
        autocomplete: "off", spellcheck: "false", maxlength: "120", class: "pp-manual" });
      let select = null;
      if (avail.available) {
        const opt = (s) => h("option", { value: s }, s);
        select = h("select", { "aria-label": `Choose a notify service for ${p.name}`, class: "pp-select" },
          h("option", { value: "" }, services.length || entities.length ? "Choose a service…" : "No other services in Home Assistant"),
          services.length ? h("optgroup", { label: "Notify actions" }, services.map(opt)) : null,
          entities.length ? h("optgroup", { label: "Notify entities" }, entities.map(opt)) : null,
          h("option", { value: "__manual" }, "Type a name…"));
        manual.hidden = true;
        select.addEventListener("change", () => { manual.hidden = select.value !== "__manual"; if (!manual.hidden) manual.focus(); });
      }
      const result = h("div", { class: "pp-result", role: "status" });
      const addBtn = h("button", { type: "button", class: o.buttonClass || "btn-secondary btn-small" }, "Add");
      const add = async () => {
        let v = (select && select.value !== "__manual" ? select.value : manual.value).trim();
        if (!v) { toast("Choose or type a notify service first.", true); return; }
        if (!v.includes(".")) v = "notify." + v;
        if (!NOTIFY_RE.test(v)) { toast("A notify service looks like notify.mobile_app_phone (lower-case letters, digits and _).", true); return; }
        addBtn.disabled = true;
        try { changed(await o.api(o.path(p), { method: "POST", body: { service: v } })); toast(`Added ${v}`); draw(); }
        catch (e) { fail(e); addBtn.disabled = false; }
      };
      addBtn.addEventListener("click", add);
      manual.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
      const reachable = (p.notify || []).length + usable();
      const test = h("button", { type: "button", class: o.ghostClass || "btn-ghost btn-small", disabled: !reachable,
        title: reachable ? "Send a short test notification to their phones and every service listed"
          : "Link a phone in Home Assistant or add a notify service first" }, "Send a test");
      test.addEventListener("click", async () => {
        test.disabled = true;
        try {
          const r = await o.api(o.testPath(p), { method: "POST", body: {} });
          const { list, text } = resultsText(r && r.results);
          result.textContent = text;
          const bad = list.filter((y) => !y.ok).map((y) => y.service);
          if (bad.length) toast(`Sent, but Home Assistant refused ${bad.join(", ")}`, true); else toast(`Test sent to ${p.name}`);
        } catch (e) { fail(e); }
        setTimeout(() => { test.disabled = false; }, 1500);
      });
      const extras = (p.notify || []).length
        ? p.notify.map((s) => h("span", { class: "pp-chip" }, s,
          h("button", { type: "button", "aria-label": `Remove ${s} from ${p.name}`, title: "Remove", onclick: async () => {
            try { changed(await o.api(`${o.path(p)}/${encodeURIComponent(s)}`, { method: "DELETE" })); toast("Removed"); draw(); }
            catch (e) { fail(e); }
          } }, "✕")))
        : [h("span", { class: "pp-hint" }, texts.none ? texts.none(p, usable())
          : (usable() ? "None — only for something else, like a speaker or a second service."
            : "None — nothing is sent to them until a phone is linked in Home Assistant or a service is added here."))];
      const phones = p.ha && p.ha.phones ? p.ha.phones : [];
      mount(box,
        texts.intro ? h("p", { class: "pp-hint" }, texts.intro(p)) : null,
        h("div", { class: "pp-line" }, h("span", { class: "pp-label" }, "Phones"),
          h("div", { class: "pp-chips" }, phoneChips(p, false), o.extraLine ? o.extraLine(p) : null)),
        phones.some((ph) => !ph.service) ? h("p", { class: "pp-hint" }, "⚠ Companion app action not found: Home Assistant has no notify.mobile_app_… action for that phone yet — open the Companion app on it once.") : null,
        texts.phonesHelp ? h("p", { class: "pp-hint" }, typeof texts.phonesHelp === "function" ? texts.phonesHelp(p) : texts.phonesHelp) : null,
        h("div", { class: "pp-line" }, h("span", { class: "pp-label" }, "Also"), h("div", { class: "pp-chips" }, extras)),
        avail.available || o.hideUnavailable ? null : h("div", { class: "pp-warn" }, (avail.error || "Home Assistant's notify services couldn't be listed.") + " You can still type one, e.g. notify.mobile_app_phone."),
        h("div", { class: "pp-add" }, select, manual, addBtn, test),
        result);
    };
    draw();
    return box;
  }

  async function notifyDialog(p, o) {
    let services = o.services;
    if (o.loadServices) {
      try { services = await o.loadServices(); } catch (e) { services = null; /* a name can still be typed */ }
    }
    return o.openModal(`Notifications — ${p.name}`, notifyEditor(p, Object.assign({}, o, { services })), { onClose: o.onClose });
  }

  function render(box, opts) {
    const o = Object.assign({ cardClass: "card" }, opts || {});
    // what reaches them: their usable phones plus the extra services
    const reach = (p) => (o.notify && o.notify.usable ? o.notify.usable(p) : linkedPhones(p)) + (p.notify || []).length;
    const checkLink = o.checkAgain ? h("button", { type: "button", class: "link-btn", id: "checkHaAgain", onclick: async (e) => {
      e.target.disabled = true;
      try { await o.checkAgain(); } finally { e.target.disabled = false; }
    } }, "Check Home Assistant again") : null;
    const intro = [].concat(o.intro || []).map((x) => (typeof x === "string" ? h("p", null, x) : x));
    if (checkLink) intro.push(h("p", null, checkLink));
    // inline editors: say once, above the list, that HA's notify services couldn't be listed
    const inline = o.notify && o.notify.mode !== "dialog" ? Object.assign({}, o.notify, { hideUnavailable: true }) : o.notify;
    const sv = o.notify && o.notify.services;
    if (inline && inline !== o.notify && sv && !sv.available) {
      intro.push(h("p", { class: "pp-warn", id: "notifyUnavailable" },
        (sv.error || "Home Assistant's notify services couldn't be listed.") + " You can still type a service name, e.g. notify.mobile_app_phone.",
        o.notify.retry ? [" ", h("button", { type: "button", class: "link-btn", onclick: o.notify.retry }, "Try again")] : null));
    }
    const cards = (o.people || []).map((p) => {
      const x = o.person ? o.person(p) || {} : {};
      const badges = (x.badges || []).filter(Boolean).map(([text, kind]) => h("span", { class: "pp-badge" + (kind ? " " + kind : "") }, text));
      const notify = o.notify
        ? (o.notify.mode === "dialog"
          ? h("button", { type: "button", class: o.notify.buttonClass || "btn-secondary btn-small", title: "Phones, extra notify services and a test",
            onclick: () => notifyDialog(p, o.notify) }, "🔔 Notifications", reach(p) ? ` (${reach(p)})` : null)
          : h("div", { class: "pp-block" }, notifyEditor(p, inline)))
        : null;
      return h("div", { class: `${o.cardClass} pp-person`.trim(), id: `person-${p.id}`, dataset: { userId: p.id } },
        h("div", { class: "pp-head" }, x.avatar || null,
          h("div", { class: "pp-who" }, h("span", { class: "pp-name" }, p.name), badges,
            x.sub ? h("div", { class: "pp-sub" }, x.sub) : null),
          x.controls ? h("div", { class: "pp-controls" }, x.controls) : null),
        [].concat(x.blocks || []).filter(Boolean).map((b) => h("div", { class: "pp-block" }, b)),
        (x.actions || (notify && o.notify.mode === "dialog"))
          ? h("div", { class: "pp-actions" }, o.notify && o.notify.mode === "dialog" ? notify : null, x.actions) : null,
        o.notify && o.notify.mode !== "dialog" ? notify : null);
    });
    mount(box, h("div", { class: "pp-page" },
      intro.length ? h("div", { class: "pp-intro" }, intro) : null,
      cards.length ? h("div", { class: "pp-list" }, cards) : h("div", { class: "pp-empty" }, o.empty || "Nobody has opened the app yet.")));
    return box;
  }

  window.PeoplePage = { render, notifyEditor, notifyDialog, phoneChips, accessSwitch, resultsText };
})();
