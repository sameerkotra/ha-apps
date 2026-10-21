/* Household Phones — the page (spec/SPEC.md §8).
 *
 * Views (the address's #fragment, so a reload stays where it was):
 *   #/               My phone: your phones, and Check <owner>'s phone for phones that forward into your chats
 *   #/mine/<id>      one of your phones: pauses, quiet times, the setup checklist, what admins see, the log, forwarding
 *   #/check/<id>     Check now's live result (Household Chat and the Household Assistant link here)
 *   #/phones         admins: All phones
 *   #/phone/<id>     admins: a phone's page (every sensor, history, its settings)
 *   #/admin/<tab>    admins: App settings, People, Tests, Storage
 * Everything is built with DOM nodes (UI.h): names and texts are never parsed as HTML.
 */
(function () {
  "use strict";
  const { h, $, $$, clear, mount } = UI;

  const api = UI.makeApi({
    networkError: "Couldn't reach the app. Check the connection.",
    message: (body, res) => UI.errorMessage(body && body.detail, res.status),
  });
  const toast = (msg, error) => UI.toast(msg, { error: !!error });
  const openModal = (title, content, opts) => UI.openModal(title, content, Object.assign({ escape: "stack" }, opts || {}));

  const state = { me: null, view: "my", adminTab: "settings", timers: [], showIgnored: false };
  const isAdmin = () => !!(state.me && state.me.user.isAdmin);

  HouseholdTheme.bindSelect($("#theme-select"));

  function markCurrent(el, on) {
    el.classList.toggle("active", on);
    if (on) el.setAttribute("aria-current", "page"); else el.removeAttribute("aria-current");
  }

  // ---------- small menus (⋯ on a row, the name button's menu): each is a layer the back gesture closes first ----------
  const menus = [];
  function closeMenus() { menus.slice().forEach((m) => m.close()); }
  function toggleMenu(anchor, items) {
    const mine = menus.find((m) => m.anchor === anchor);
    closeMenus();
    if (mine) return null;
    let closed = false;
    const el = h("div", { class: "pop-menu", role: "menu" }, items.filter(Boolean).map((it) => it.el ? it.el :
      h("button", { type: "button", role: "menuitem", class: "pop-item" + (it.danger ? " danger" : ""),
        "aria-current": it.current ? "page" : null, disabled: !!it.disabled,
        onclick: () => { close(); it.onclick(); } }, it.label)));
    const onDown = (e) => { if (!el.contains(e.target) && !anchor.contains(e.target)) close(); };
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus(); } };
    const onResize = () => close();
    function close() {
      if (closed) return;
      closed = true;
      el.remove();
      document.removeEventListener("pointerdown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", onResize);
      anchor.setAttribute("aria-expanded", "false");
      const i = menus.indexOf(handle);
      if (i >= 0) menus.splice(i, 1);
    }
    const handle = { close, el, anchor };
    menus.push(handle);
    document.body.append(el);
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth, ht = el.offsetHeight;
    el.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + "px";
    el.style.top = Math.max(8, r.bottom + 4 + ht <= window.innerHeight - 8 ? r.bottom + 4 : r.top - 4 - ht) + "px";
    anchor.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", onDown, true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("resize", onResize);
    const first = el.querySelector("button:not([disabled]), select");
    if (first) first.focus();
    return handle;
  }
  function moreBtn(label, items) {
    return h("button", { type: "button", class: "icon-btn more-btn", title: "More", "aria-label": label, "aria-haspopup": "menu",
      "aria-expanded": "false", onclick: (e) => { e.stopPropagation(); toggleMenu(e.currentTarget, items()); } }, "⋯");
  }

  // the name button: Me (how the app sees you), the theme, Admin
  let menuTheme = null;
  $("#userBtn").addEventListener("click", (e) => {
    if (!menuTheme) menuTheme = HouseholdTheme.bindSelect(h("select", { class: "theme-select", "aria-label": "Theme" }));
    toggleMenu(e.currentTarget, [
      state.me ? { el: h("div", { class: "pop-head" }, state.me.user.name) } : null,
      { label: "👤 How the app sees you", onclick: openWhoami },
      { el: h("label", { class: "pop-item pop-theme" }, h("span", null, "🎨 Theme"), menuTheme) },
      isAdmin() ? { label: "🛡️ Admin", current: state.view === "admin", onclick: () => go("#/admin") } : null,
    ]);
  });

  // ---------- views ----------
  const VIEWS = ["my", "mine", "check", "phones", "phone", "admin"];
  const TAB_OF = { my: "my", mine: "my", check: "my", phones: "phones", phone: "phones", admin: "admin" };
  function stopTimers() { state.timers.forEach((t) => clearInterval(t)); state.timers = []; }
  function every(ms, fn) { const t = setInterval(fn, ms); state.timers.push(t); return t; }

  function show(view) {
    state.view = view;
    stopTimers();
    for (const v of VIEWS) $(`#${v}View`).hidden = v !== view;
    $$("#topnav .tab").forEach((b) => markCurrent(b, b.dataset.view === TAB_OF[view]));
    closeMenus();
    window.scrollTo(0, 0);
  }

  function go(hash) { if (window.BackNav) BackNav.go(hash); else location.hash = hash; }
  function replace(hash) { if (window.BackNav) BackNav.replace(hash); else location.replace(hash); }
  function back() { if (!(window.BackNav && BackNav.back())) go(parentOf(location.hash) || "#/"); }
  $$("[data-back]").forEach((b) => b.addEventListener("click", back));

  function route() {
    const r = location.hash.replace(/^#/, "") || "/";
    let m;
    if (!state.me) { show("my"); return; }
    if ((m = /^\/mine\/([a-z0-9]{1,16})$/.exec(r))) return openMine(m[1]);
    if ((m = /^\/check\/([a-z0-9]{1,16})$/.exec(r))) return openCheck(m[1]);
    if (r === "/phones" && isAdmin()) return openPhones();
    if ((m = /^\/phone\/([a-z0-9]{1,16})$/.exec(r)) && isAdmin()) return openPhone(m[1]);
    if ((m = /^\/admin(?:\/([a-z]+))?$/.exec(r)) && isAdmin()) return openAdmin(m[1] || "settings");
    openMy();
  }

  $("#homeBtn").addEventListener("click", () => go("#/"));
  $$("#topnav .tab").forEach((b) => b.addEventListener("click", () => go(b.dataset.view === "my" ? "#/" : `#/${b.dataset.view}`)));

  // ---------- small helpers ----------
  // append without clearing (UI.mount clears first); null / false skipped, arrays flattened, text as text
  function add(el, ...kids) {
    const one = (k) => {
      if (k === null || k === undefined || k === false) return;
      if (Array.isArray(k)) k.forEach(one); else el.append(k);
    };
    kids.forEach(one);
    return el;
  }
  function badge(s) { return h("span", { class: `badge ${s.status}` }, s.label); }
  function dot(status) { return h("span", { class: `dot ${status}`, "aria-hidden": "true" }); }
  function batteryText(s) {
    if (s.battery === null || s.battery === undefined) return null;
    return `${s.charging ? "⚡" : "🔋"} ${s.battery}%${s.charging ? " charging" : ""}`;
  }
  function facts(list) { return h("div", { class: "facts" }, list.filter(Boolean).map((x) => h("span", null, x))); }
  function hhmm(ts) { if (!ts) return ""; const d = new Date(ts * 1000); return d.toTimeString().slice(0, 5); }
  function field(label, control, hint) {
    return h("label", { class: "field" }, h("span", { class: "field-label" }, label), control, hint ? h("span", { class: "hint small" }, hint) : null);
  }
  function card(title, ...kids) { return h("div", { class: "card" }, title ? h("h3", null, title) : null, ...kids); }
  function statusLine(s) {
    if (s.hidden) return h("div", { class: "status-line" }, badge(s));
    return h("div", { class: "status-line" }, badge(s),
      h("span", { class: "dim" }, s.lastCheckinText ? `last check-in ${s.lastCheckinText}` : "no check-in yet"));
  }
  async function runCheck(id) { go(`#/check/${id}`); }

  // ---------- who am I ----------
  async function openWhoami() {
    const body = h("div", null);
    UI.loading(body);
    openModal("How the app sees you", body);
    try {
      const w = await api("api/whoami");
      mount(clear(body), HouseholdWhoami.panel(w, { appName: "Household Phones", classes: { copy: "btn-ghost" } }));
    } catch (e) { mount(clear(body), UI.errorBox(e)); }
  }

  // ---------- My phone ----------
  async function openMy() {
    show("my");
    const box = $("#myBody");
    if (!box.firstChild) UI.loading(box);
    let r;
    try { r = await api("api/my"); } catch (e) { mount(clear(box), UI.errorBox(e, openMy)); return; }
    if (state.view !== "my") return;
    clear(box);
    if (!r.phones.length) {
      add(box, UI.empty("No phone of yours yet",
        "Install the Home Assistant Companion app on your phone and sign in to this Home Assistant: your phone shows up " +
        "here within a few minutes. (A phone of yours listed under someone else? Ask an admin to set its owner.)"));
    }
    for (const s of r.phones) add(box, myCard(s));
    if (r.others.length) {
      add(box, h("h3", { class: "section-title" }, "Phones that forward into your chats"));
      for (const o of r.others) {
        add(box, h("div", { class: "card" }, h("div", { class: "card-head" },
          h("div", null, h("div", { class: "phone-name" }, `${o.owner}'s phone`), h("div", { class: "dim small" },
            `${o.name} · forwards into ${o.chat || "a chat"}`)),
          h("button", { type: "button", class: "btn-primary", onclick: () => runCheck(o.id) }, `Check ${o.owner}'s phone`))));
      }
    }
    add(box, myQuiet(r));
    every(30000, () => { if (state.view === "my") refreshMy(); });
  }
  async function refreshMy() {
    try {
      const r = await api("api/my");
      if (state.view !== "my") return;
      const box = $("#myBody");
      $$(".my-card", box).forEach((el) => {
        const s = r.phones.find((x) => x.id === el.dataset.id);
        if (s) el.replaceWith(myCard(s));
      });
    } catch (e) { /* the next round tries again */ }
  }

  function myCard(s) {
    return h("div", { class: "card my-card", dataset: { id: s.id } },
      h("div", { class: "card-head" },
        h("div", null, h("div", { class: "phone-name" }, s.name), statusLine(s)),
        h("button", { type: "button", class: "btn-primary", onclick: () => runCheck(s.id) }, "Check now")),
      s.reasonText ? h("p", { class: "note warn" }, s.reasonText) : null,
      s.paused ? h("p", { class: "note" }, s.paused.kind === "watching" ? `You paused being watched ${s.paused.untilText}.`
        : `Alerts paused ${s.paused.untilText}.`) : null,
      facts([batteryText(s), s.network ? `📶 ${s.network}` : null, s.lastReboot ? `🔄 last restart ${s.lastReboot.replace("T", " ").slice(0, 16)}` : null,
        s.forwarding ? "💬 forwarding on" : null]),
      h("div", { class: "card-actions" }, h("a", { class: "btn-secondary", href: `#/mine/${s.id}` }, "Settings, checklist and log ›")));
  }

  function myQuiet(r) {
    const qs = h("input", { type: "time", value: r.quietStart || "" });
    const qe = h("input", { type: "time", value: r.quietEnd || "" });
    return h("details", { class: "card about" }, h("summary", null, "🔕 Quiet hours for alerts you receive"),
      h("p", { class: "hint" }, "Alerts that reach you during these hours wait until they end. Empty: the household's."),
      h("div", { class: "grid2" }, field("From", qs), field("Until", qe)),
      h("div", { class: "card-actions" }, h("button", { type: "button", class: "btn-secondary", onclick: async () => {
        try { await api("api/me/quiet", { method: "PUT", body: { quietStart: qs.value, quietEnd: qe.value } }); toast("Saved"); }
        catch (e) { toast(e.message, true); }
      } }, "Save")));
  }

  // ---------- one of my phones ----------
  async function openMine(id) {
    show("mine");
    const box = $("#mineBody");
    UI.loading(box);
    let p;
    try { p = await api(`api/phones/${id}`); } catch (e) { mount(clear(box), UI.errorBox(e, () => openMine(id))); return; }
    if (state.view !== "mine") return;
    if (!p.mine) { replace(`#/phone/${id}`); return; }
    renderMine(p);
  }

  function renderMine(p) {
    const box = clear($("#mineBody"));
    const again = () => openMine(p.id);
    const todo = p.checklist.filter((c) => c.ok === false).length;
    add(box, 
      h("div", { class: "card" }, h("div", { class: "card-head" },
        h("div", null, h("h2", { class: "view-title" }, p.name), statusLine(p)),
        h("button", { type: "button", class: "btn-primary", onclick: () => runCheck(p.id) }, "Check now")),
        p.reasonText ? h("p", { class: "note warn" }, p.reasonText) : null,
        facts([batteryText(p), p.network ? `📶 ${p.network}` : null, p.lastReboot ? `🔄 last restart ${String(p.lastReboot).replace("T", " ").slice(0, 16)}` : null])),
      pauseCard(p, again),
      checklistCard(p, todo),
      forwardingCard(p, again),
      h("div", { class: "card" }, h("h3", null, "🔕 Quiet times for this phone"),
        quietForm(p.quietStart, p.quietEnd, async (qs, qe) => {
          await api(`api/phones/${p.id}/mine`, { method: "PUT", body: { quietStart: qs, quietEnd: qe } }); toast("Saved");
        }, "When it is often switched off (e.g. 23:00–06:00): offline and battery alerts wait, and go out only if it is still offline when the quiet time ends.")),
      transparencyCard(p, again),
      p.replaceable.length ? replaceCard(p, again) : null,
      h("div", { class: "danger-zone" }, h("h3", null, "Stop watching my phone"),
        h("p", { class: "hint" }, p.stopRequested ? "You asked the admins to stop watching this phone." :
          "Sends the admins a request to stop watching this phone altogether."),
        p.stopRequested ? null : h("button", { type: "button", class: "btn-danger-outline", onclick: async () => {
          if (!await UI.confirmDialog("Ask to stop watching?", "The admins get your request; they decide.", { okLabel: "Send the request" })) return;
          try { renderMine(await api(`api/phones/${p.id}/stop-request`, { method: "POST" })); toast("Sent to the admins"); }
          catch (e) { toast(e.message, true); }
        } }, "Send the request")));
  }

  function quietForm(start, end, save, help) {
    const qs = h("input", { type: "time", value: start || "" });
    const qe = h("input", { type: "time", value: end || "" });
    return h("div", null, help ? h("p", { class: "hint" }, help) : null,
      h("div", { class: "grid2" }, field("From", qs), field("Until", qe)),
      h("div", { class: "card-actions" }, h("button", { type: "button", class: "btn-secondary", onclick: async () => {
        try { await save(qs.value, qe.value); } catch (e) { toast(e.message, true); }
      } }, "Save"), h("button", { type: "button", class: "btn-ghost", onclick: async () => {
        qs.value = ""; qe.value = "";
        try { await save("", ""); } catch (e) { toast(e.message, true); }
      } }, "No quiet time")));
  }

  function pauseCard(p, again) {
    const c = h("div", { class: "card" }, h("h3", null, "⏸️ Pause"));
    if (p.pause) {
      add(c, h("p", null, p.pause.kind === "watching" ? `You paused being watched ${p.pause.untilText}. Nothing is asked and admins see only "Paused".`
        : `Alerts are paused ${p.pause.untilText}${p.pause.reason ? ` (${p.pause.reason})` : ""}. Check-ins and Check now go on.`),
        h("button", { type: "button", class: "btn-secondary", onclick: async () => {
          try { await api(`api/phones/${p.id}/pause`, { method: "DELETE" }); again(); } catch (e) { toast(e.message, true); }
        } }, "End the pause"));
      return c;
    }
    const pause = async (body) => {
      try { await api(`api/phones/${p.id}/pause`, { method: "POST", body }); again(); } catch (e) { toast(e.message, true); }
    };
    const reason = h("input", { type: "text", maxlength: "80", placeholder: "Why (optional) — e.g. on a flight" });
    const until = h("input", { type: "time" });
    add(c, h("p", { class: "hint one-line" }, "Pause alerts when a silence is expected. The status stays visible."),
      field("Reason", reason),
      h("div", { class: "chips" },
        [1, 4, 12].map((n) => h("button", { type: "button", class: "chip-btn", onclick: () => pause({ kind: "alerts", hours: n, reason: reason.value }) }, `${n} h`)),
        h("button", { type: "button", class: "chip-btn", onclick: () => pause({ kind: "alerts", untilOnline: true, reason: reason.value }) }, "Until it's back online")),
      h("div", { class: "card-actions" }, field("Until a time", until), h("button", { type: "button", class: "btn-secondary", onclick: () => {
        if (!until.value) { toast("Choose a time", true); return; }
        const [hh, mm] = until.value.split(":").map(Number);
        const d = new Date(); d.setHours(hh, mm, 0, 0); if (d <= new Date()) d.setDate(d.getDate() + 1);
        pause({ kind: "alerts", until: d.getTime() / 1000, reason: reason.value });
      } }, "Pause until then")));
    if (p.canPauseWatching) {
      add(c, h("details", { class: "fold" }, h("summary", null, "Pause being watched"),
        h("p", { class: "hint" }, `Up to ${p.pauseMaxHours} hours: no requests, no alerts, and admins see only "Paused by you".`),
        h("div", { class: "chips" }, [1, 4, 12, 24].filter((n) => n <= p.pauseMaxHours).map((n) =>
          h("button", { type: "button", class: "chip-btn", onclick: () => pause({ kind: "watching", hours: n }) }, `${n} h`)))));
    }
    return c;
  }

  function checklistCard(p, todo) {
    const mark = (ok) => ok === true ? "✅" : ok === false ? "⚠️" : "•";
    return h("details", { class: "card", open: todo ? true : null },
      h("summary", { class: "h3-like" }, h("strong", null, `🧰 Setup checklist${todo ? ` — ${todo} to fix` : ""}`)),
      h("ul", { class: "checklist" }, p.checklist.map((c) => h("li", null, h("span", { class: "mark", "aria-label": c.ok === true ? "done" : c.ok === false ? "to fix" : "check" }, mark(c.ok)),
        h("div", null, h("div", { class: "item-title" }, c.title), h("div", { class: "hint small" }, c.help),
          c.adb ? h("pre", { class: "code" }, c.adb) : null)))));
  }

  function transparencyCard(p, again) {
    const share = PeoplePage.accessSwitch(p.sharePlace, async (on, t) => {
      try { await api(`api/phones/${p.id}/mine`, { method: "PUT", body: { sharePlace: on } }); }
      catch (e) { t.checked = !on; toast(e.message, true); }
    }, { label: "Share my last place" });
    const log = p.log.map((l) => h("div", { class: "log-row" }, h("span", { class: "dim" }, l.atText),
      h("span", null, `Check now by ${l.by}${l.via !== "app" ? ` (${l.via.replace("_", " ")})` : ""} — ${l.result}`)));
    const alertsLog = p.alertsLog.map((l) => h("div", { class: "log-row" }, h("span", { class: "dim" }, l.atText), h("span", null, l.text)));
    return h("div", { class: "card" }, h("h3", null, "👀 What admins see"),
      h("ul", { class: "plain-list" }, p.adminsSee.map((x) => h("li", null, x))),
      h("div", { class: "switch-row" }, h("span", null, "📍 Share my last place (the Home Assistant zone, in offline alerts too)"), share),
      h("details", { class: "fold" }, h("summary", null, `Who checked your phone (${p.log.length})`),
        log.length ? h("div", { class: "log-list" }, log) : h("p", { class: "hint" }, "Nobody yet.")),
      h("details", { class: "fold" }, h("summary", null, `Alerts about your phone (${p.alertsLog.length})`),
        alertsLog.length ? h("div", { class: "log-list" }, alertsLog) : h("p", { class: "hint" }, "None in the history kept.")));
  }

  function replaceCard(p, again) {
    return h("div", { class: "card" }, h("h3", null, "🔁 A new phone?"),
      h("p", { class: "hint" }, "If this phone replaces an older one, its name, settings, forwarding and history move here and the old one is retired."),
      h("div", { class: "chips" }, p.replaceable.map((o) => h("button", { type: "button", class: "chip-btn", onclick: async () => {
        if (!await UI.confirmDialog(`Replaces ${o.name}?`, `${o.name}'s name, settings, forwarding rules and history move to this phone.`, { okLabel: "Replace" })) return;
        try { await api(`api/phones/${p.id}/replaces`, { method: "POST", body: { old: o.id } }); toast("Done"); again(); }
        catch (e) { toast(e.message, true); }
      } }, `Replaces ${o.name}`))));
  }

  // ---------- Forward my messages (the owner's own switch) ----------
  const DISAPPEAR = [["1h", "1 hour"], ["1d", "1 day"], ["7d", "7 days"], ["30d", "30 days"]];
  function forwardingCard(p, again) {
    const f = p.forwarding;
    const c = h("div", { class: "card" }, h("h3", null, "💬 Forward my messages"));
    if (p.ios) { add(c, h("p", { class: "hint" }, "iPhones can't forward messages: the Companion app has no Last notification sensor there.")); return c; }
    if (!f.allowed) { add(c, h("p", { class: "hint" }, "Forwarding is turned off in this household.")); return c; }
    const save = async (body, msg) => {
      try { renderMine(Object.assign(await api(`api/phones/${p.id}/forwarding`, { method: "PUT", body }), {})); if (msg) toast(msg); }
      catch (e) { toast(e.message, true); }
    };
    const sw = PeoplePage.accessSwitch(f.on, (on, t) => { t.disabled = true; save({ on }, on ? "Forwarding is on" : "Forwarding is off"); },
      { label: "Forward my messages" });
    add(c, h("div", { class: "switch-row" }, h("span", null, "A text (a one-time code…) arriving on this phone goes into Household Chat as a disappearing message. The text is never kept by this app."), sw));
    if (f.error) add(c, h("p", { class: "note bad" }, f.error));
    if (!f.on) return c;
    const target = f.target === "my_room" ? "📌 My room (only you)" : `${f.chatName || "a chat"}${f.members ? ` — ${f.members} people will see your forwarded messages` : ""}`;
    add(c, facts([`Into: ${target}`, `Disappear after ${(DISAPPEAR.find((d) => d[0] === f.disappear) || [0, f.disappear])[1]}`,
      f.last ? `Last forward ${f.last.atText} into ${f.last.chat}` : "Nothing forwarded yet"]));
    // target
    const pick = h("button", { type: "button", class: "btn-secondary", onclick: () => chooseChat(p, save) }, "Choose the chat…");
    // disappear
    const dis = h("select", { onchange: () => save({ disappear: dis.value }, "Saved") },
      DISAPPEAR.map(([v, l]) => h("option", { value: v, selected: v === f.disappear ? true : null }, l)));
    // filter
    const filter = h("select", null, [["codes", "Codes only (a 4–8 digit code next to code, OTP, PIN…)"], ["words", "Containing words I choose"],
      ["all", "Everything"]].map(([v, l]) => h("option", { value: v, selected: v === f.filter ? true : null }, l)));
    const words = h("input", { type: "text", value: f.words.join(", "), placeholder: "parcel, delivery" });
    const wordsField = field("Words (comma-separated)", words);
    wordsField.hidden = f.filter !== "words";
    filter.addEventListener("change", () => { wordsField.hidden = filter.value !== "words"; });
    const saveFilter = h("button", { type: "button", class: "btn-secondary", onclick: async () => {
      const body = { filter: filter.value };
      if (filter.value === "words") body.words = words.value.split(",").map((w) => w.trim()).filter(Boolean);
      if (filter.value === "all" && !f.allConfirmed) {
        if (!await UI.confirmDialog("Forward everything?", `All your texts will be forwarded into ${f.target === "my_room" ? "your My room" : f.chatName}.`, { okLabel: "Forward everything", danger: true })) return;
        body.allConfirmed = true;
      }
      save(body, "Saved");
    } }, "Save the filter");
    // apps
    const apps = h("textarea", { rows: "3" }, f.apps.join("\n"));
    const saveApps = h("button", { type: "button", class: "btn-secondary", onclick: () =>
      save({ apps: apps.value.split(/\s+/).map((a) => a.trim()).filter(Boolean) }, "Saved") }, "Save the apps");
    const quiet = h("div", null, quietForm(f.quietStart, f.quietEnd, (qs, qe) => save({ quietStart: qs, quietEnd: qe }, "Saved"),
      "Forwards that arrive in these hours…"));
    const mode = h("select", { onchange: () => save({ quietMode: mode.value }, "Saved") },
      [["hold", "are sent when the quiet time ends"], ["drop", "are dropped"]].map(([v, l]) => h("option", { value: v, selected: v === f.quietMode ? true : null }, l)));
    add(c, h("div", { class: "card-actions" }, pick),
      field("Disappear after", dis),
      h("details", { class: "fold" }, h("summary", null, "What to forward"), field("Filter", filter), wordsField, h("div", { class: "card-actions" }, saveFilter)),
      h("details", { class: "fold" }, h("summary", null, `Which apps (${f.apps.length})`),
        h("p", { class: "hint" }, "Android package names, one per line: Google Messages com.google.android.apps.messaging, Samsung Messages com.samsung.android.messaging, WhatsApp com.whatsapp, Signal org.thoughtcrime.securesms. The Companion app's own notifications are never forwarded."),
        apps, h("div", { class: "card-actions" }, saveApps)),
      h("details", { class: "fold" }, h("summary", null, "Quiet hours for forwards"), quiet, field("…and then they", mode)));
    return c;
  }

  async function chooseChat(p, save) {
    const body = h("div", null);
    UI.loading(body, "Asking Household Chat…");
    const dlg = openModal("Forward into…", body);
    let rid;
    try { rid = (await api(`api/phones/${p.id}/forwarding/chats`, { method: "POST" })).request; }
    catch (e) { mount(clear(body), UI.errorBox(e)); return; }
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      let r;
      try { r = await api(`api/requests/${rid}`); } catch (e) { mount(clear(body), UI.errorBox(e)); return; }
      if (r.state === "pending") continue;
      if (r.state === "failed") { mount(clear(body), UI.errorBox(r.error)); return; }
      const chats = r.result.chats;
      const f = p.forwarding;
      const rule = f.chats;
      const listed = (f.groups || []).map((g) => g.toLowerCase());
      const ok = (c) => c.kind === "personal" || (rule === "member") || (rule === "listed" && listed.includes(c.name.toLowerCase()));
      mount(clear(body), h("p", { class: "hint" }, "Best: a small group just for this (e.g. \"Phone codes\") with the people who need the codes. A group starts on \"Codes only\"."),
        h("div", { class: "radio-list" }, chats.filter(ok).map((c) => h("button", { type: "button", class: "chip-btn", onclick: async () => {
          if (c.kind !== "personal" && !await UI.confirmDialog(`Forward into ${c.name}?`, `${c.members} people will see your forwarded messages.`, { okLabel: "Forward there" })) return;
          dlg.close();
          save(c.kind === "personal" ? { target: "my_room" } : { target: "chat", chatId: c.id, chatName: c.name, chatKind: c.kind }, "Saved");
        } }, `${c.icon || (c.kind === "personal" ? "📌" : c.kind === "direct" ? "👤" : "👥")} ${c.name} · ${c.members} ${c.members === 1 ? "person" : "people"}`))),
        rule !== "member" ? h("p", { class: "hint small" }, rule === "own_room" ? "In this household forwards go only into your own My room." : "An admin allows only some groups.") : null);
      return;
    }
    mount(clear(body), UI.errorBox("Household Chat didn't answer — is it running?"));
  }

  // ---------- Check now: the live result card ----------
  async function openCheck(id) {
    show("check");
    const box = $("#checkBody");
    UI.loading(box, "Asking the phone…");
    let cid;
    try { cid = (await api(`api/phones/${id}/check`, { method: "POST" })).check; }
    catch (e) {
      if (e.status === 429 || e.status === 409) {
        try {
          const last = await api(`api/check/${id}/latest`);
          if (last.check) { renderCheck(last.check, id, e.message); return; }
        } catch (e2) { /* the error below */ }
      }
      mount(clear(box), UI.errorBox(e, () => openCheck(id)));
      return;
    }
    pollCheck(cid, id);
  }

  function pollCheck(cid, id) {
    let busy = false;
    const tick = async () => {
      if (busy || state.view !== "check") return;
      busy = true;
      try {
        const c = await api(`api/checks/${cid}`);
        if (state.view !== "check") return;
        renderCheck(c, id);
        if (c.state !== "waiting" && !c.forward) stopTimers();
      } catch (e) { stopTimers(); mount(clear($("#checkBody")), UI.errorBox(e, () => openCheck(id))); }
      finally { busy = false; }
    };
    tick();
    every(1500, tick);
  }

  function renderCheck(c, id, note) {
    const box = clear($("#checkBody"));
    const pct = Math.min(100, Math.round((c.elapsed / c.waitS) * 100));
    add(box, h("div", { class: "card check-card" },
      h("div", { class: "dim" }, `Check now · ${c.phone} · asked by ${c.byName} at ${hhmm(c.started)}`),
      h("div", { class: `check-title ${c.state}`, role: "status" }, c.title),
      c.text ? h("p", null, c.text) : null,
      c.state === "waiting" ? h("div", { class: "progress", "aria-hidden": "true" }, h("span", { style: `width:${pct}%` })) : null,
      facts([c.battery !== null && c.battery !== undefined ? `${c.charging ? "⚡" : "🔋"} ${c.battery}%${c.charging ? ", charging" : ""}` : null,
        c.network ? `📶 on ${c.network}` : null, c.lastReboot ? `🔄 last restart ${String(c.lastReboot).replace("T", " ").slice(0, 16)}` : null]),
      c.forward ? h("p", { class: "dim" }, `💬 ${c.forward}`) : null,
      c.forwardNow ? h("div", { class: "just-forwarded" }, `📨 ${c.forwardNow}`) : null,
      note ? h("p", { class: "note warn" }, note) : null,
      c.state !== "waiting" ? h("div", { class: "card-actions" }, h("button", { type: "button", class: "btn-primary", onclick: () => openCheck(id) },
        c.state === "online" ? "Check again" : "Try again")) : null),
      c.state === "no_reply" ? h("p", { class: "hint" }, "The phone is now watched as overdue: it is asked again, and marked Offline if it stays silent.") : null);
  }

  // ---------- Admin mode: All phones ----------
  async function openPhones() {
    show("phones");
    const box = $("#phonesBody");
    if (!box.firstChild) UI.loading(box);
    await loadPhones();
    every(30000, () => { if (state.view === "phones") loadPhones(); });
  }
  async function loadPhones(data) {
    const box = $("#phonesBody");
    let r = data;
    if (!r) {
      try { r = await api("api/admin/phones"); } catch (e) { mount(clear(box), UI.errorBox(e, () => loadPhones())); return; }
    }
    if (state.view !== "phones") return;
    renderPhones(r);
  }
  $("#refreshBtn").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try { await loadPhones(await api("api/admin/refresh", { method: "POST" })); toast("Read from Home Assistant"); }
    catch (err) { toast(err.message, true); }
    finally { e.currentTarget.disabled = false; }
  });

  function kpi(label, value, cls) {
    return h("div", { class: "kpi" }, h("div", { class: "kpi-label" }, label), h("div", { class: "kpi-value" + (cls ? " " + cls : "") }, String(value)));
  }

  function renderPhones(r) {
    const box = clear($("#phonesBody"));
    const s = r.summary;
    add(box, h("div", { class: "summary-strip", "aria-label": `${s.phones} phones, ${s.online} online, ${s.offline} offline, ${s.paused} paused` },
      kpi("Phones", s.phones), kpi("Online", s.online, "pos"), kpi("Offline", s.offline, s.offline ? "neg" : ""),
      kpi("Late", s.late), kpi("Paused", s.paused)));
    if (r.lostContact) {
      add(box, h("p", { class: "note bad" }, `Home Assistant has lost contact with the phones (${r.lostContact.silent} of ${r.lostContact.total} silent since ${hhmm(r.lostContact.since)}). Offline alerts wait until contact returns.`));
    }
    if (!r.haLoaded) add(box, h("p", { class: "note warn" }, "Home Assistant's devices haven't been read yet (or can't be: is the app running inside Home Assistant?)."));
    if (r.new.length) {
      add(box, h("h3", { class: "section-title" }, `New devices (${r.new.length})`));
      add(box, h("div", { class: "strip-cards" }, r.new.map((p) => newDevice(p))));
    }
    if (r.unused.length) {
      add(box, h("h3", { class: "section-title" }, "Not used lately"));
      add(box, h("div", { class: "card" }, r.unused.map((p) => h("div", { class: "new-device" },
        h("div", { class: "grow" }, h("div", { class: "phone-name" }, p.name), h("div", { class: "dim small" }, p.lastCheckinText ? `last check-in ${p.lastCheckinText}` : "never checked in")),
        h("button", { type: "button", class: "btn-secondary", onclick: () => adminAction(`api/admin/phones/${p.id}/keep`, "POST") }, "Keep"),
        h("button", { type: "button", class: "btn-danger-outline", onclick: async () => {
          if (!await UI.confirmDialog(`Remove ${p.name}?`, "Its history is deleted with it. If Home Assistant still has the device, it comes back as a new device.", { okLabel: "Remove", danger: true })) return;
          adminAction(`api/admin/phones/${p.id}`, "DELETE");
        } }, "Remove")))));
    }
    if (!r.phones.length && !r.tablets.length) {
      add(box, UI.empty("No phones yet", "Phones appear once the Home Assistant Companion app is installed and signed in on them.",
        { label: "↻ Read Home Assistant again", onclick: () => $("#refreshBtn").click() }));
    }
    const list = (title, items) => {
      if (!items.length) return;
      add(box, h("div", { class: "card" }, h("div", { class: "ui-group-head" }, h("span", null, title), h("span", { class: "ui-group-total" }, String(items.length))),
        h("div", { class: "phone-list" }, items.map(phoneRow))));
    };
    list("Phones", r.phones);
    list("Tablets", r.tablets);
    if (r.ignored.length) {
      const b = h("button", { type: "button", class: "link-btn", onclick: () => { state.showIgnored = !state.showIgnored; renderPhones(r); } },
        state.showIgnored ? "Hide ignored" : `Show ignored (${r.ignored.length})`);
      add(box, h("p", null, b));
      if (state.showIgnored) list("Ignored", r.ignored);
    }
    add(box, h("details", { class: "about" }, h("summary", null, "Setup notes"),
      h("p", null, `Check-ins come from ${r.checkins === "events" ? "Home Assistant's state_reported events" : "a once-a-minute look at the phones' last_reported times (Home Assistant hasn't sent state_reported events)"}.`),
      h("p", null, "Home Assistant itself keeps the Last notification sensor's text in its history. To keep forwarded messages out of it, exclude the sensors from the recorder in configuration.yaml:"),
      h("pre", { class: "code" }, "recorder:\n  exclude:\n    entity_globs:\n      - sensor.*_last_notification\n      - sensor.*_last_removed_notification"),
      h("p", null, r.connected.chat ? "Household Chat is connected." : "Household Chat isn't connected: forwarding and 📱 Check phone in Chat need it.")));
  }

  function newDevice(p) {
    const kind = h("select", { "aria-label": `Kind of ${p.name}` }, [["phone", "Phone (watched)"], ["tablet", "Tablet (shown, not watched)"], ["ignored", "Ignored (hidden)"]]
      .map(([v, l]) => h("option", { value: v, selected: v === p.kind ? true : null }, l)));
    return h("div", { class: "card new-device" },
      h("div", { class: "grow" }, h("div", { class: "phone-name" }, p.name), h("div", { class: "dim small" }, [p.manufacturer, p.model, p.owner ? `owner ${p.owner}` : "no owner yet"].filter(Boolean).join(" · "))),
      kind, h("button", { type: "button", class: "btn-primary", onclick: async () => {
        try { await api(`api/admin/phones/${p.id}`, { method: "PUT", body: { kind: kind.value, confirm: true } }); loadPhones(); }
        catch (e) { toast(e.message, true); }
      } }, "Confirm"));
  }

  async function adminAction(path, method, body) {
    try { renderPhones(await api(path, { method, body })); } catch (e) { toast(e.message, true); }
  }

  function phoneRow(s) {
    const sub = s.hidden ? s.label : [s.owner, s.model].filter(Boolean).join(" · ");
    return h("div", { class: "phone-row" },
      h("button", { type: "button", class: "row-open", onclick: () => go(`#/phone/${s.id}`) },
        dot(s.status),
        h("div", { class: "row-main" }, h("div", { class: "phone-name" }, s.name),
          h("div", { class: "dim small" }, s.hidden ? sub : `${s.label}${s.lastCheckinText ? ` · ${s.lastCheckinText}` : ""} · ${sub}`)),
        s.hidden ? null : h("div", { class: "row-cols" }, h("span", null, batteryText(s) || "—"), h("span", null, s.network || "—"),
          h("span", null, s.appVersion || s.osVersion || ""))),
      moreBtn(`More for ${s.name}`, () => [
        { label: "🔎 Check now", disabled: s.hidden, onclick: () => runCheck(s.id) },
        { label: "⏸️ Pause alerts for 4 hours", disabled: s.hidden || !!s.paused, onclick: async () => {
          try { await api(`api/phones/${s.id}/pause`, { method: "POST", body: { kind: "alerts", hours: 4 } }); loadPhones(); } catch (e) { toast(e.message, true); } } },
        { label: "✏️ Edit", onclick: () => go(`#/phone/${s.id}`) },
        s.watched ? { label: "Stop watching", danger: true, onclick: async () => {
          if (!await UI.confirmDialog(`Stop watching ${s.name}?`, "No requests and no alerts for it; it stays listed.", { okLabel: "Stop watching", danger: true })) return;
          try { await api(`api/admin/phones/${s.id}`, { method: "PUT", body: { watched: false } }); loadPhones(); } catch (e) { toast(e.message, true); } } }
          : { label: "Watch it", onclick: async () => {
            try { await api(`api/admin/phones/${s.id}`, { method: "PUT", body: { watched: true } }); loadPhones(); } catch (e) { toast(e.message, true); } } },
      ]));
  }

  // ---------- Admin mode: a phone's page ----------
  async function openPhone(id) {
    show("phone");
    const box = $("#phoneBody");
    UI.loading(box);
    let r;
    try { r = await api(`api/phones/${id}`); } catch (e) { mount(clear(box), UI.errorBox(e, () => openPhone(id))); return; }
    if (state.view !== "phone") return;
    renderPhone(r.admin, r);
  }

  async function peopleList() {
    try { return (await api("api/admin/people")).people; } catch (e) { return []; }
  }

  async function renderPhone(a, whole) {
    const box = clear($("#phoneBody"));
    const again = () => openPhone(a.id);
    add(box, h("div", { class: "card" }, h("div", { class: "card-head" },
      h("div", null, h("h2", { class: "view-title" }, a.name), statusLine(a)),
      a.hidden ? null : h("button", { type: "button", class: "btn-primary", onclick: () => runCheck(a.id) }, "Check now")),
      a.reasonText ? h("p", { class: "note warn" }, a.reasonText) : null,
      a.stopRequested ? h("p", { class: "note warn" }, `${a.owner || "The owner"} asked to stop watching this phone.`) : null,
      a.hidden ? h("p", { class: "hint" }, "The owner paused being watched: its status, battery, network and sensors aren't shown until the pause ends.") :
        facts([batteryText(a), a.network ? `📶 ${a.network}` : null, a.lastReboot ? `🔄 ${String(a.lastReboot).replace("T", " ").slice(0, 16)}` : null,
          a.zone ? `📍 ${a.zone}` : null, a.forwardingTarget ? `💬 forwards into ${a.forwardingTarget}` : null])));
    add(box, card("📱 Device", h("table", { class: "sensor-table" }, [["Manufacturer", a.manufacturer], ["Model", a.model],
      ["System", [a.osName, a.osVersion].filter(Boolean).join(" ")], ["Companion app", a.appVersion || "—"], ["Owner", a.owner || "nobody"],
      ["Kind", a.kind], ["In Home Assistant as", a.haName], ["Notify service", a.notifyService || a.defaultService || "—"]]
      .map(([k, v]) => h("tr", null, h("td", null, k), h("td", null, v || "—"))))));
    add(box, await settingsCard(a, again));
    if (a.sensors) {
      const c = card("📟 Every sensor");
      for (const g of a.sensors.groups) {
        add(c, h("div", { class: "ui-group-head" }, h("span", null, g.label), h("span", { class: "ui-group-total" }, String(g.sensors.length))),
          h("table", { class: "sensor-table" }, g.sensors.map((x) => h("tr", null, h("td", null, x.name),
            h("td", null, x.value === null ? "—" : `${x.value}${x.unit ? " " + x.unit : ""}`)))));
      }
      if (!a.sensors.groups.length) add(c, h("p", { class: "hint" }, "Home Assistant hasn't sent this phone's sensors yet."));
      if (a.sensors.off.length) add(c, h("div", { class: "ui-group-head" }, h("span", null, "Off on the phone"), h("span", { class: "ui-group-total" }, String(a.sensors.off.length))),
        h("p", { class: "hint" }, a.sensors.off.join(", ") + " — the owner can switch them on in the Companion app (Manage sensors)."));
      add(box, c);
    }
    const hist = card(`🗂️ History`);
    let day = null;
    if (!a.history.length) add(hist, h("p", { class: "hint" }, "Nothing in the history kept."));
    for (const x of a.history) {
      const d = x.atText.slice(0, 10);
      if (d !== day) { day = d; add(hist, h("div", { class: "ui-group-head" }, h("span", null, d))); }
      add(hist, h("div", { class: "log-row" }, h("span", { class: "dim" }, x.atText.slice(11)),
        h("span", null, x.text + (x.battery !== null && x.battery !== undefined ? ` · 🔋 ${x.battery}%` : ""))));
    }
    add(box, hist);
    add(box, h("div", { class: "danger-zone" }, h("h3", null, "Stop watching or remove"),
      h("p", { class: "hint" }, "Stop watching: no requests and no alerts for it. Remove: its history is deleted; it comes back as a new device if Home Assistant still has it."),
      h("div", { class: "card-actions" },
        a.watched ? h("button", { type: "button", class: "btn-danger-outline", onclick: async () => {
          try { await api(`api/admin/phones/${a.id}`, { method: "PUT", body: { watched: false } }); again(); } catch (e) { toast(e.message, true); }
        } }, "Stop watching") : null,
        h("button", { type: "button", class: "btn-danger-outline", onclick: async () => {
          if (!await UI.confirmDialog(`Remove ${a.name}?`, "Its history is deleted with it.", { okLabel: "Remove", danger: true })) return;
          try { await api(`api/admin/phones/${a.id}`, { method: "DELETE" }); go("#/phones"); } catch (e) { toast(e.message, true); }
        } }, "Remove"))));
    if (whole && whole.mine) add(box, h("p", null, h("a", { href: `#/mine/${a.id}` }, "Your own settings for this phone ›")));
  }

  async function settingsCard(a, again) {
    const people = await peopleList();
    const name = h("input", { type: "text", value: a.name, maxlength: "60" });
    const kind = h("select", null, [["phone", "Phone"], ["tablet", "Tablet"], ["ignored", "Ignored"]].map(([v, l]) => h("option", { value: v, selected: v === a.kind ? true : null }, l)));
    const watched = h("input", { type: "checkbox", checked: a.watched });
    const interval = h("select", null, [["", "The household's"], ["15", "15 minutes"], ["10", "10 minutes"], ["5", "5 minutes"]]
      .map(([v, l]) => h("option", { value: v, selected: String(a.intervalOwn || "") === v ? true : null }, l)));
    const owner = h("select", null, [h("option", { value: "" }, "Nobody")].concat(people.map((p) =>
      h("option", { value: p.id, selected: p.id === a.ownerId ? true : null }, p.name))));
    const svc = h("input", { type: "text", value: a.notifyService || "", placeholder: a.defaultService || "mobile_app_…" });
    return h("details", { class: "card" }, h("summary", null, h("strong", null, "⚙️ This phone's settings")),
      h("div", { class: "form" }, field("Name", name, "Empty: \"<owner>'s <model>\"."), h("div", { class: "grid2" }, field("Kind", kind), field("Check-in interval", interval)),
        h("label", { class: "check" }, watched, "Watched (asked to report, alerts)"),
        field("Owner", owner, "Who Home Assistant says registered it; correct it here. Changing the owner switches forwarding off (only the owner switches it on)."),
        field("Notify service", svc, "Only if Home Assistant names this phone's notify service differently."),
        h("div", { class: "card-actions" }, h("button", { type: "button", class: "btn-primary", onclick: async () => {
          const body = { name: name.value, kind: kind.value, watched: watched.checked, intervalMin: interval.value ? Number(interval.value) : null,
            ownerId: owner.value || null, notifyService: svc.value || null };
          try { await api(`api/admin/phones/${a.id}`, { method: "PUT", body }); toast("Saved"); again(); } catch (e) { toast(e.message, true); }
        } }, "Save"))));
  }

  // ---------- Admin ----------
  $$("#adminTabs .tab").forEach((b) => b.addEventListener("click", () => replace(b.dataset.tab === "settings" ? "#/admin" : `#/admin/${b.dataset.tab}`)));
  const ADMIN_TABS = { settings: loadSettings, people: loadPeople, tests: loadTests, storage: () => {} };

  function openAdmin(tab) {
    if (!ADMIN_TABS[tab]) tab = "settings";
    state.adminTab = tab;
    show("admin");
    $$("#adminTabs .tab").forEach((b) => markCurrent(b, b.dataset.tab === tab));
    for (const t of Object.keys(ADMIN_TABS)) $(`#tab-${t}`).hidden = t !== tab;
    ADMIN_TABS[tab]();
  }

  async function loadSettings() {
    const box = $("#tab-settings");
    if (!box.firstChild) UI.loading(box);
    try {
      await SettingsPage.render(box, {
        load: () => api("api/admin/settings"),
        save: (body) => api("api/admin/settings", { method: "PUT", body }),
        classes: { card: "card", primary: "btn-primary", secondary: "btn-secondary", ghost: "btn-ghost" },
        footer: "Changes apply right away. Who is an admin is set on the app's Configuration tab (admin_users).",
        toast: (m, e) => toast(m, e),
      });
    } catch (e) { mount(clear(box), UI.errorBox(e, loadSettings)); }
  }

  async function loadPeople(refresh) {
    const box = $("#tab-people");
    if (!box.firstChild) UI.loading(box);
    let r;
    try { r = await api("api/admin/people" + (refresh ? "?refresh=1" : "")); } catch (e) { mount(clear(box), UI.errorBox(e, () => loadPeople(refresh))); return; }
    PeoplePage.render(box, {
      people: r.people,
      intro: ["Everyone who has opened this app. Mark a child: a child's phone is watched without the child being able to pause it (they still see what admins see).",
        "📱 Alerts go to each person's phones from Home Assistant (Settings → People → the person → Track device), never to the phone the alert is about. 🔔 adds an extra notify service and sends a test."],
      checkAgain: async () => { await loadPeople(true); toast("Read from Home Assistant"); },
      person: (p) => ({
        badges: [p.you ? ["you"] : null, p.isAdmin ? ["admin", "accent"] : null, p.isChild ? ["child", "accent"] : null],
        sub: `${p.phones} ${p.phones === 1 ? "phone" : "phones"}` + (p.username ? ` · ${p.username}` : ""),
        controls: h("label", { class: "pp-toggle", title: p.isAdmin ? "Admins can't be marked as children" : "" }, "Child ",
          PeoplePage.accessSwitch(p.isChild, async (on, t) => {
            try { await api(`api/admin/people/${encodeURIComponent(p.id)}`, { method: "PUT", body: { isChild: on } }); p.isChild = on; }
            catch (e) { t.checked = !on; toast(e.message, true); }
          }, { label: `${p.name} is a child`, disabled: p.isAdmin })),
      }),
      notify: {
        mode: "dialog", buttonClass: "btn-secondary", ghostClass: "btn-ghost", openModal, api,
        toast: (m, err) => toast(m, err), fail: (e) => toast(e.message, true),
        loadServices: () => api("api/admin/notify-services"),
        path: (p) => `api/admin/people/${encodeURIComponent(p.id)}/notify`,
        testPath: (p) => `api/admin/people/${encodeURIComponent(p.id)}/notify/test`,
        texts: { intro: (p) => `Where ${p.name}'s phone alerts go.` },
        onClose: () => loadPeople(),
      },
      empty: "Nobody has opened the app yet.",
    });
  }

  const ALERT_TESTS = [["offline", "Offline"], ["app", "App not reporting"], ["lost_contact", "Lost contact with all phones"],
    ["back", "Back online"], ["restart", "Restarted"], ["battery", "Battery low"], ["charge", "Charging reminder"]];
  async function loadTests() {
    const box = clear($("#tab-tests"));
    const c = card("🔔 Test an alert", h("p", { class: "hint" }, "A sample of each alert, to you, the way alerts are delivered (App settings → Deliver alerts by)."));
    for (const [id, label] of ALERT_TESTS) {
      add(c, h("div", { class: "test-row" }, h("span", null, label), h("button", { type: "button", class: "btn-secondary", onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { const r = await api("api/admin/test-alert", { method: "POST", body: { alert: id } }); toast(`Sent: ${r.sent.join(", ")}`); }
        catch (err) { toast(err.message, true); }
        finally { e.currentTarget.disabled = false; }
      } }, "Test")));
    }
    add(box, c);
    let conn = null;
    try { conn = await api("api/admin/connected"); } catch (e) { /* shown below */ }
    add(box, card("💬 Test a forward",
      h("p", { class: "hint" }, "Posts \"📱 Test forward\" into your own 📌 My room in Household Chat, disappearing after 1 hour — the same way a phone's forward goes."),
      h("p", { class: "dim small" }, conn ? (conn.chat ? "Household Chat is connected." : "Household Chat isn't connected (not installed, not running, or app messages are off).") : ""),
      h("button", { type: "button", class: "btn-secondary", onclick: async () => {
        try { await api("api/admin/test-forward", { method: "POST" }); toast("Sent to your My room"); } catch (e) { toast(e.message, true); }
      } }, "Send a test forward")));
  }

  $("#importDb").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    if (!await UI.confirmDialog("Restore this backup?", "Every phone, setting and history is replaced by the backup's.", { okLabel: "Restore", danger: true })) return;
    const fd = new FormData();
    fd.append("file", file);
    try { await api("api/admin-storage-import-db", { method: "POST", formData: fd }); toast("Restored"); await init(); }
    catch (err) { toast(err.message, true); }
  });

  // ---------- start ----------
  async function loadMe() {
    state.me = await api("api/me");
    $("#userName").textContent = state.me.user.name;
    $("#phonesTab").hidden = !isAdmin();
    $("#adminTab").hidden = !isAdmin();
    $("#startingBanner").hidden = !state.me.starting;
    const lost = state.me.lostContact;
    $("#lostBanner").hidden = !(lost && isAdmin());
    if (lost) $("#lostBanner").textContent = `⚠️ Home Assistant has lost contact with the phones (${lost.silent} of ${lost.total} silent since ${hhmm(lost.since)}).`;
    HouseholdWhoami.fillNoAdminBanner($("#noAdminBanner"), state.me.noAdmin, state.me.user.name, { onOpen: openWhoami });
  }

  async function init() {
    const note = $("#meError");
    try { await loadMe(); }
    catch (e) {
      state.me = null;
      show("my");
      clear($("#myBody"));
      note.hidden = false;
      mount(clear(note), h("div", { class: "note warn" }, h("h3", null, "Can't identify a Home Assistant user"),
        h("p", null, "Open the app from its page in the Home Assistant sidebar."),
        UI.errorBox(e, () => { UI.loading(note); init().then(startDeepLink); })));
      return;
    }
    note.hidden = true;
    clear(note);
    route();
  }

  let deepLinkStarted = false;
  function startDeepLink() {
    if (deepLinkStarted || !state.me || !window.HouseholdDeepLink) return;
    deepLinkStarted = true;
    HouseholdDeepLink.start(state.me.page, (r) => (/^\/(check|phone|mine)\/[a-z0-9]{1,16}$/.test(r) || r === "/phones" ? r : null),
      (r) => go("#" + r));
  }

  // Top-level tabs start a new Back trail; a deep link with no trail goes back to its parent page
  const TAB_HASHES = new Set(["", "#", "#/", "#/phones", "#/admin"]);
  function parentOf(hash) {
    if (/^#\/(mine|check)\//.test(hash)) return "#/";
    if (/^#\/phone\//.test(hash)) return "#/phones";
    if (/^#\/admin\/[a-z]+$/.test(hash)) return "#/admin";
    return null;
  }

  window.addEventListener("hashchange", route);
  if (window.BackNav) {
    BackNav.init({
      atHome: () => state.view === "my",
      goHome: () => { history.replaceState(history.state, "", "#/"); route(); },
      openLayers: () => [...UI.dialogs(), ...menus],
      closeLayer: (m) => m.close(),
      isTab: (hash) => TAB_HASHES.has(hash),
      parentOf,
    });
  }
  init().then(startDeepLink);
})();
