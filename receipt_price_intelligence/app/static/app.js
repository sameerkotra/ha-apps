/* Shared helpers for all pages. Exposes a single global: RPI.
   All URLs are relative to the page so the app works under Home Assistant ingress. */
(function () {
  'use strict';

  // Pages live at the app root (/, /capture.html, /review.html), so the directory of the
  // current page is the app root, whatever prefix ingress adds.
  const BASE = location.pathname.slice(0, location.pathname.lastIndexOf('/') + 1);
  const url = (path) => BASE + String(path).replace(/^\//, '');
  // The app version the server baked into this page (data-app-version on this script's tag).
  const BAKED_VERSION = (document.currentScript && document.currentScript.dataset.appVersion) || '';

  // fetch wrapper (common/ui.js): JSON in and out; errors carry .status and the parsed body (.data).
  const api = UI.makeApi({
    url,
    init: ({ method = 'GET', body, form } = {}) => {
      const init = { method, headers: {} };
      if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      if (form) init.body = form;
      return init;
    },
    networkError: 'Could not reach the server. Check your connection.',
    message: (j, res) => {
      let detail = res.statusText || 'Request failed';
      try {
        if (typeof j.detail === 'string') detail = j.detail;
        else if (Array.isArray(j.detail)) detail = j.detail.map((d) => d.msg).join('; ');
      } catch (_) { /* not JSON */ }
      return detail;
    },
    makeError: (msg, status, data, res) => {
      const err = new Error(msg);
      if (res) { err.status = status; err.data = data; }
      return err;
    },
  });

  // ---- DOM builder: text is always set via textContent/createTextNode, never innerHTML ----
  // common/ui.js's h(), refusing raw HTML and script addresses in links.
  function h(tag, attrs, ...children) {
    const safe = {};
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === 'html') throw new Error('html attribute is not allowed');
      // Links can come from web search results (untrusted): never let a script address through.
      if (k === 'href' && /^\s*(javascript|data|vbscript):/i.test(String(v).replace(/[\u0000-\u001f]/g, ''))) continue;
      safe[k] = v;
    }
    return UI.h(tag, safe, ...children);
  }

  // Trusted static SVG icons only (never interpolate data into these).
  const ICONS = {
    back: '<path d="M15 18l-6-6 6-6" />',
    camera: '<path d="M4 8h3l2-2h6l2 2h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="M21 16l-5-5-8 8"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 4l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.5v.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.01"/>',
    bell: '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-12M9 7V4h6v3"/>',
    receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"/>',
    home: '<path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    map: '<path d="M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
    car: '<path d="M5 16v-4l2-5h10l2 5v4"/><path d="M3 16h18v3H3z"/><circle cx="7.5" cy="19" r="1.6"/><circle cx="16.5" cy="19" r="1.6"/>',
    download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    upload: '<path d="M12 16V5M7 9l5-5 5 5M5 20h14"/>',
    file: '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h4"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    chart: '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
    tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    up: '<path d="M6 15l6-6 6 6"/>',
    down: '<path d="M6 9l6 6 6-6"/>',
    store: '<path d="M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6"/>',
    shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.3-7.5 9.5-4.3-1.2-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2 2 4-4"/>',
    cart: '<path d="M3 4h2l2.4 10.2a1 1 0 0 0 1 .8h8.9a1 1 0 0 0 1-.8L20 8H6.2"/><circle cx="9.5" cy="19" r="1.4"/><circle cx="17" cy="19" r="1.4"/>',
  };
  function icon(name, extraClass) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.9');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    if (extraClass) svg.setAttribute('class', extraClass);
    svg.innerHTML = ICONS[name] || ''; // static strings above only
    return svg;
  }

  // ---- formatting ----
  function money(n, currency) {
    if (n == null || Number.isNaN(n)) return '—';
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || 'USD' }).format(n);
    } catch (_) {
      return (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2);
    }
  }
  function fmtDate(iso) {
    if (!iso) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
    if (!m) return iso;
    const d = new Date(+m[1], +m[2] - 1, +m[3]);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }
  function relTime(iso) {
    if (!iso) return '';
    const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso.replace(' ', 'T') + 'Z');
    const s = (Date.now() - t.getTime()) / 1000;
    if (!isFinite(s)) return '';
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    return t.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function parseNumber(str) {
    // '' -> null, '1,234.50' -> 1234.5, '$3' -> 3, junk -> NaN
    const s = String(str ?? '').trim();
    if (!s) return null;
    const neg = /^\(.*\)$|^-/.test(s);
    const cleaned = s.replace(/[^0-9.]/g, '');
    if (!cleaned || (cleaned.match(/\./g) || []).length > 1) return NaN;
    const n = parseFloat(cleaned);
    return neg ? -n : n;
  }
  const fixed2 = (n) => (n == null || Number.isNaN(n) ? '' : n.toFixed(2));
  const trimNum = (n) => String(+Number(n).toFixed(3));

  // ---- draft helpers ----
  const STATUS = {
    PROCESSING: { label: 'Reading…', kind: 'info' },
    NEEDS_REVIEW: { label: 'Needs review', kind: 'warn' },
    EXTRACTION_FAILED: { label: 'Failed', kind: 'danger' },
    APPROVED: { label: 'Saved', kind: 'ok' },
    REJECTED: { label: 'Discarded', kind: '' },
  };
  function statusPill(status, editingSaved) {
    // A saved receipt reopened for editing is in review again, but is still in the history.
    const s = editingSaved && status === 'NEEDS_REVIEW' ? { label: 'Editing saved', kind: 'warn' } : (STATUS[status] || { label: status, kind: '' });
    return h('span', { class: 'pill ' + s.kind }, h('span', { class: 'dot' }), s.label);
  }
  function parseExtraction(draft) {
    if (!draft || !draft.raw_llm_output) return {};
    try {
      const v = JSON.parse(draft.raw_llm_output);
      return v && typeof v === 'object' ? v : {};
    } catch (_) { return {}; }
  }

  // ---- arithmetic checks (mirror receipt_llm.py so the server and this UI agree) ----
  const TOL = 0.02;
  const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
  const differs = (a, b, tol = TOL) => Math.round(Math.abs(a - b) * 100) / 100 > tol;
  const f2 = (n) => n.toFixed(2);

  function itemMathIssue(it) {
    const unit = isNum(it.unit_price) ? it.unit_price : null;
    const sub = isNum(it.line_subtotal) ? it.line_subtotal : null;
    const total = isNum(it.line_total) ? it.line_total : null;
    const hasDisc = isNum(it.discount_amount);
    const disc = hasDisc ? it.discount_amount : 0;

    if (sub != null && total != null && hasDisc && differs(sub - disc, total)) {
      return `${f2(sub)} − ${f2(disc)} discount = ${f2(sub - disc)}, but the line total is ${f2(total)}`;
    }
    if (unit != null) {
      let mult = null, label = null;
      if (isNum(it.weight_value)) {
        mult = it.weight_value;
        label = `${trimNum(mult)} ${it.weight_unit || ''}`.trim();
      } else if (isNum(it.quantity)) {
        mult = it.quantity;
        label = trimNum(mult);
      }
      if (mult != null) {
        const expected = mult * unit;
        const actual = sub != null ? sub : (total != null ? total + disc : null);
        if (actual != null && differs(expected, actual, TOL + 0.005 * Math.abs(expected))) {
          return `${label} × ${f2(unit)} = ${f2(expected)}, but the line shows ${f2(actual)}`;
        }
      }
    }
    return null;
  }

  // Returns [{ok, text}] for each check that has enough data, plus itemsSum.
  function reconcile(header, items, taxes) {
    const checks = [];
    const totals = items.map((i) => i.line_total).filter(isNum);
    const itemsSum = totals.length ? Math.round(totals.reduce((a, b) => a + b, 0) * 100) / 100 : null;
    const { subtotal, tax_total: tax, grand_total: grand } = header;
    const disc = isNum(header.discount_total) ? header.discount_total : 0;
    const fees = isNum(header.fee_total) ? header.fee_total : 0;

    if (itemsSum != null && isNum(subtotal)) {
      const ok = !differs(itemsSum, subtotal) || !differs(itemsSum - disc, subtotal);
      checks.push({
        ok, id: 'items',
        text: ok ? 'Items add up to the subtotal'
          : `Items add up to ${f2(itemsSum)}, but the subtotal is ${f2(subtotal)} (${itemsSum > subtotal ? '+' : '−'}${f2(Math.abs(itemsSum - subtotal))})`,
      });
    }
    const base = isNum(subtotal) ? subtotal : itemsSum;
    if (base != null && isNum(grand)) {
      const expected = base + (isNum(tax) ? tax : 0) + fees;
      const ok = !differs(expected, grand) || !differs(expected - disc, grand);
      checks.push({
        ok, id: 'total',
        text: ok ? 'Subtotal + tax + fees equals the total'
          : `Subtotal + tax + fees = ${f2(expected)}, but the total is ${f2(grand)}`,
      });
    }
    const taxAmts = (taxes || []).map((t) => t.tax_amount).filter(isNum);
    if (taxAmts.length && isNum(tax)) {
      const sum = taxAmts.reduce((a, b) => a + b, 0);
      if (differs(sum, tax)) checks.push({ ok: false, id: 'tax', text: `Tax lines add up to ${f2(sum)}, but total tax is ${f2(tax)}` });
    }
    return { checks, itemsSum };
  }

  // ---- toast + dialog ----
  function toast(message, kind) {
    let host = document.querySelector('.toast-host');
    if (!host) { host = h('div', { class: 'toast-host', 'aria-live': 'polite' }); document.body.append(host); }
    UI.toast(message, { root: host, kind, ms: kind === 'error' ? 6000 : 3200 });
  }

  function confirmDialog({ title, body, items, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false }) {
    return new Promise((resolve) => {
      const dlg = h('dialog', { 'aria-labelledby': 'dlg-title' },
        h('div', { class: 'dialog-body' },
          h('h2', { id: 'dlg-title' }, title),
          body ? h('p', {}, body) : null,
          items && items.length ? h('ul', {}, items.map((t) => h('li', {}, t))) : null),
        h('div', { class: 'dialog-actions' },
          h('button', { class: 'btn btn-ghost', onclick: () => dlg.close('cancel') }, cancelLabel),
          h('button', { class: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'), onclick: () => dlg.close('ok') }, confirmLabel)));
      dlg.addEventListener('close', () => { resolve(dlg.returnValue === 'ok'); dlg.remove(); });
      document.body.append(dlg);
      dlg.showModal();
    });
  }

  // ---- current user and homes ----
  let mePromise = null;
  const me = () => (mePromise = mePromise || api('api/v1/me'));

  const HOME_KEY = 'rpi.home';
  function storedHome() { try { return localStorage.getItem(HOME_KEY); } catch (_) { return null; } }
  function setCurrentHome(id) { try { localStorage.setItem(HOME_KEY, id); } catch (_) { /* private mode */ } }
  // The remembered home if it still exists, else the first one.
  function pickHome(homes) {
    if (!homes.length) return null;
    const saved = storedHome();
    return (homes.find((h) => h.id === saved) || homes[0]).id;
  }

  // ---- app shell: sidebar on a computer, bottom bar on a phone ----
  // Every page is wrapped in the same shell (built here, before the page's own script runs). Pages
  // used to add their own row of section links with RPI.nav(); that now only marks the section.
  const SECTIONS = [
    { id: 'list', href: 'list.html', label: 'List', short: 'List', icon: 'check' },
    { id: 'receipts', href: 'index.html', label: 'Receipts', short: 'Receipts', icon: 'receipt' },
    { id: 'insights', href: 'analysis.html', label: 'Insights', short: 'Insights', icon: 'chart' },
    { id: 'deals', href: 'deals.html', label: 'Best prices', short: 'Prices', icon: 'tag' },
    { id: 'trip', href: 'trip.html', label: 'Trip', short: 'Trip', icon: 'map' },
    { id: 'stores', href: 'stores.html', label: 'Stores', short: 'Stores', icon: 'store' },
    { id: 'notify', href: 'notifications.html', label: 'Notify', short: 'Notify', icon: 'bell' },
    { id: 'admin', href: 'admin.html', label: 'Admin', short: 'Admin', icon: 'shield', admin: true },
  ];
  const PAGE_SECTION = {
    '': 'list', 'list.html': 'list', 'index.html': 'receipts', 'capture.html': 'receipts', 'review.html': 'receipts',
    'import.html': 'receipts', 'analysis.html': 'insights', 'deals.html': 'deals', 'trip.html': 'trip',
    'stores.html': 'stores', 'notifications.html': 'notify', 'admin.html': 'admin', 'backup.html': 'admin',
    'debug.html': 'admin', 'whoami.html': '',
  };
  const PAGE = location.pathname.slice(location.pathname.lastIndexOf('/') + 1);

  // Moving between pages replaces the current history entry instead of adding one, so the back
  // gesture in the Home Assistant app goes to the List (the start page), then out of the app
  // (backnav.js). When backnav has its extra entry in place, it is removed first and the page
  // then opens in its place (goHome below picks up the address).
  const NEXT_KEY = 'rpi.next';
  let leaving = false;   // a page is opening in this one's place: backnav must not add its entry again
  window.addEventListener('pageshow', (e) => { if (e.persisted) leaving = false; });
  try { sessionStorage.removeItem(NEXT_KEY); } catch (_) { /* a page that just opened has nowhere pending */ }
  function go(href) {
    const url = new URL(href, location.href);
    const target = url.href;
    if (url.pathname === location.pathname && url.search === location.search) {   // same page, another tab of it
      if (target !== location.href) {
        const old = location.href;
        history.replaceState(history.state, '', target);
        window.dispatchEvent(new HashChangeEvent('hashchange', { oldURL: old, newURL: target }));
      }
      return;
    }
    const guarded = history.state && history.state.backnav && !document.querySelector('dialog[open]');
    if (guarded && window.BackNav) {
      try { sessionStorage.setItem(NEXT_KEY, target); } catch (_) { location.replace(target); return; }
      leaving = true;
      history.back();
      setTimeout(() => {   // in case Back didn't reach us
        try { sessionStorage.removeItem(NEXT_KEY); } catch (_) { /* ignore */ }
        location.replace(target);
      }, 600);
      return;
    }
    leaving = true;
    location.replace(target);
  }
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.target || a.hasAttribute('download')) return;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || /^[a-z]+:/i.test(href) || href.startsWith('/')) return;
    const u = new URL(href, location.href);
    if (u.origin !== location.origin || !/(\.html|\/)$/.test(u.pathname)) return;
    e.preventDefault();
    go(u.href);
  });

  let shellNav = null, adminLink = null, userName = null, banner = null;
  function sectionLink(sec) {
    const a = h('a', { class: 'side-tab', href: sec.href, title: sec.label, 'aria-current': PAGE_SECTION[PAGE] === sec.id ? 'page' : null,
      hidden: sec.admin ? true : null },
    icon(sec.icon), h('span', { class: 'label' }, sec.label), h('span', { class: 'short', 'aria-hidden': 'true' }, sec.short));
    if (sec.admin) adminLink = a;
    return a;
  }
  function buildShell() {
    if (document.querySelector('.app')) return;
    const main = h('div', { class: 'main-col' });
    banner = h('div', { class: 'setup-banner', role: 'alert', hidden: true });
    main.append(banner);
    for (const node of [...document.body.childNodes]) {
      if (node.nodeType === 1 && (node.tagName === 'SCRIPT' || node.tagName === 'DIALOG')) continue;
      main.append(node);
    }
    const collapse = h('button', { type: 'button', class: 'sidebar-collapse-btn', title: 'Collapse the menu', 'aria-label': 'Collapse the menu' }, '‹');
    collapse.addEventListener('click', () => {
      const collapsed = !HouseholdTheme.sidebarCollapsed();
      HouseholdTheme.setSidebarCollapsed(collapsed);
      collapse.textContent = collapsed ? '›' : '‹';
    });
    if (HouseholdTheme.sidebarCollapsed()) collapse.textContent = '›';
    // The page theme (static/common/theme-boot.js): shared with the other household apps.
    const theme = HouseholdTheme.bindSelect(h('select', { id: 'theme-select', class: 'theme-select', title: 'Theme', 'aria-label': 'Theme' }));
    userName = h('span', { class: 'sidebar-user-name' }, '…');
    shellNav = h('nav', { class: 'side-tabs', 'aria-label': 'Sections' }, SECTIONS.map(sectionLink));
    const side = h('aside', { class: 'sidebar' }, collapse,
      h('div', { class: 'brand' }, h('span', { class: 'brand-mark' }, icon('cart')), h('span', { class: 'brand-name' }, 'Shopping')),
      shellNav,
      h('div', { class: 'sidebar-footer' },
        h('a', { class: 'sidebar-user', href: 'whoami.html', title: 'How the app sees you' },
          h('span', { class: 'sidebar-user-label' }, 'Signed in as'), userName),
        h('label', { class: 'theme-pick' }, 'Theme', theme)));
    const app = h('div', { class: 'app' }, side, main);
    document.body.prepend(app);
  }
  function loadWhoamiScript() {
    if (window.HouseholdWhoami) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const v = BAKED_VERSION ? '?v=' + encodeURIComponent(BAKED_VERSION) : '';
      const s = h('script', { src: 'static/common/whoami.js' + v });
      s.addEventListener('load', resolve);
      s.addEventListener('error', reject);
      document.head.appendChild(s);
    });
  }
  function fillShell(who) {
    if (!userName) return;
    userName.textContent = who.display_name || who.username || 'you';
    if (adminLink) adminLink.hidden = !who.is_admin;
    if (who.noAdmin && banner) {
      // "No admin yet": the shared banner (common/whoami.js), loaded only when it is needed
      loadWhoamiScript().then(() => HouseholdWhoami.fillNoAdminBanner(banner, true, who.username || who.id, { href: 'whoami.html' }))
        .catch(() => { /* the banner just stays hidden */ });
    }
  }
  // Older pages call RPI.nav('section'); the shell already shows the sections.
  function nav() { return document.createTextNode(''); }

  // Admin pages share a row of tabs.
  const ADMIN_TABS = [['settings', 'admin.html', 'App settings'], ['homes', 'admin.html#homes', 'Homes and people'],
    ['backup', 'backup.html', 'Backup'], ['debug', 'debug.html', 'Web debug']];
  function adminTabs(active) {
    return h('nav', { class: 'admin-tabs', 'aria-label': 'Admin' },
      ADMIN_TABS.map(([id, href, label]) => h('a', { class: 'admin-tab', href, 'aria-current': id === active ? 'page' : null }, label)));
  }

  function backNav() {
    if (!window.BackNav) return;
    const home = PAGE === '' || PAGE === 'list.html';
    window.BackNav.init({
      atHome: () => home || leaving,
      goHome: () => {
        let next = null;
        try { next = sessionStorage.getItem(NEXT_KEY); sessionStorage.removeItem(NEXT_KEY); } catch (_) { /* ignore */ }
        leaving = true;
        location.replace(next || BASE);
      },
      openLayers: () => (leaving ? [] : [...document.querySelectorAll('dialog[open], .combo-list')]),
      closeLayer: (el) => { if (el.tagName === 'DIALOG') el.close('cancel'); else el.remove(); },
    });
  }

  // Signed-in user, all homes, and the home to show (remembered choice or the first).
  async function homeContext() {
    const [who, homes] = await Promise.all([me(), api('api/v1/homes')]);
    return { me: who, homes, homeId: pickHome(homes) };
  }
  function homeSwitcher(homes, homeId, onChange) {
    if (homes.length < 2) return null;
    const sel = h('select', { class: 'select home-select', 'aria-label': 'Home' }, homes.map((x) => h('option', { value: x.id }, x.name)));
    sel.value = homeId;
    sel.addEventListener('change', () => { setCurrentHome(sel.value); onChange(sel.value); });
    return sel;
  }

  // ---- autocomplete on an existing <input> ----
  // options(): [{label, hint?}] . onPick(option|null, text): option is null when the person
  // chose to use the typed text as a new value. The dropdown is fixed-positioned on <body> so
  // cards with overflow:hidden can't clip it.
  let comboSeq = 0;
  function attachCombobox(input, { options, onPick, allowCreate = true, createLabel = (t) => `Use “${t}”`, max = 8 }) {
    const listId = 'combo-' + (++comboSeq);
    let list = null, shown = [], active = -1;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('autocomplete', 'off');

    function compute() {
      const text = input.value.trim();
      const needle = text.toLowerCase();
      let opts = options() || [];
      if (needle) {
        const starts = opts.filter((o) => o.label.toLowerCase().startsWith(needle));
        const contains = opts.filter((o) => !o.label.toLowerCase().startsWith(needle) && o.label.toLowerCase().includes(needle));
        opts = starts.concat(contains);
      }
      shown = opts.slice(0, max).map((o) => ({ ...o, _opt: o }));
      const exact = opts.some((o) => o.label.toLowerCase() === needle);
      if (allowCreate && text && !exact) shown.push({ label: createLabel(text), create: true });
    }
    function position() {
      if (!list) return;
      const r = input.getBoundingClientRect();
      list.style.left = r.left + 'px';
      list.style.top = (r.bottom + 4) + 'px';
      list.style.width = Math.max(r.width, 220) + 'px';
    }
    function close() {
      if (list) { list.remove(); list = null; }
      active = -1;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
    }
    function pick(i) {
      const o = shown[i];
      if (!o) return;
      if (o.create) onPick && onPick(null, input.value.trim());
      else { input.value = o.label; onPick && onPick(o._opt, o.label); }
      input.dispatchEvent(new Event('input', { bubbles: true }));
      close();
    }
    function paint() {
      compute();
      if (!shown.length) return close();
      if (!list) {
        list = h('div', { class: 'combo-list', role: 'listbox', id: listId });
        (input.closest('dialog') || document.body).append(list);  // inside a modal dialog it must live in the dialog to be visible
        input.setAttribute('aria-expanded', 'true');
        input.setAttribute('aria-controls', listId);
      }
      list.replaceChildren(...shown.map((o, i) => h('div', {
        class: 'combo-opt' + (o.create ? ' create' : ''), role: 'option', id: `${listId}-${i}`,
        'aria-selected': String(i === active),
        onmousedown: (e) => { e.preventDefault(); pick(i); },
      }, h('span', {}, o.label), o.hint ? h('span', { class: 'hint' }, o.hint) : null)));
      position();
    }

    input.addEventListener('input', () => { active = -1; paint(); });
    input.addEventListener('focus', paint);
    input.addEventListener('blur', () => setTimeout(close, 120));
    input.addEventListener('keydown', (e) => {
      if (!list) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % shown.length; paint(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + shown.length) % shown.length; paint(); }
      else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(active); }
      else if (e.key === 'Escape') { close(); }
    });
    window.addEventListener('scroll', position, true);
    window.addEventListener('resize', position);
    return { close };
  }

  // ---- small charts (inline SVG, no libraries) ----
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svgEl(name, attrs) {
    const el = document.createElementNS(SVG_NS, name);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }
  function sparkline(values, { w = 84, h: ht = 26, direction = 'flat' } = {}) {
    const pts = values.filter((v) => typeof v === 'number');
    const svg = svgEl('svg', { viewBox: `0 0 ${w} ${ht}`, width: w, height: ht, class: 'spark ' + direction, 'aria-hidden': 'true' });
    if (pts.length < 2) return svg;
    const min = Math.min(...pts), max = Math.max(...pts), span = max - min || 1;
    const path = pts.map((v, i) => `${(i / (pts.length - 1) * (w - 4) + 2).toFixed(1)},${(ht - 3 - (v - min) / span * (ht - 6)).toFixed(1)}`).join(' ');
    svg.append(svgEl('polyline', { points: path, fill: 'none', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
    return svg;
  }
  const CHART_COLORS = ['#34d399', '#6ea8fe', '#f5b942', '#f2697b', '#b28dff', '#5fd0d6'];
  // series: [{label, points:[{date, price}]}]
  function lineChart(series, { w = 520, h: ht = 190, format = (n) => n.toFixed(2) } = {}) {
    const all = series.flatMap((s) => s.points.map((p) => ({ t: new Date(p.date + 'T00:00:00').getTime(), v: p.price })));
    const svg = svgEl('svg', { viewBox: `0 0 ${w} ${ht}`, class: 'linechart', role: 'img', 'aria-label': 'Price over time' });
    if (!all.length) return svg;
    const padL = 44, padR = 10, padT = 10, padB = 24;
    const tMin = Math.min(...all.map((p) => p.t)), tMax = Math.max(...all.map((p) => p.t));
    let vMin = Math.min(...all.map((p) => p.v)), vMax = Math.max(...all.map((p) => p.v));
    if (vMin === vMax) { vMin *= 0.95; vMax *= 1.05; }
    const x = (t) => padL + (tMax === tMin ? (w - padL - padR) / 2 : (t - tMin) / (tMax - tMin) * (w - padL - padR));
    const y = (v) => padT + (1 - (v - vMin) / (vMax - vMin)) * (ht - padT - padB);
    for (let i = 0; i <= 3; i++) {
      const v = vMin + (vMax - vMin) * i / 3, yy = y(v);
      svg.append(svgEl('line', { x1: padL, x2: w - padR, y1: yy, y2: yy, class: 'grid' }));
      const t = svgEl('text', { x: padL - 6, y: yy + 4, 'text-anchor': 'end', class: 'axis' }); t.textContent = format(v); svg.append(t);
    }
    const fmt = (t) => new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    for (const [t, anchor, xx] of [[tMin, 'start', padL], [tMax, 'end', w - padR]]) {
      const el = svgEl('text', { x: xx, y: ht - 6, 'text-anchor': anchor, class: 'axis' }); el.textContent = fmt(t); svg.append(el);
    }
    series.forEach((s, i) => {
      const color = CHART_COLORS[i % CHART_COLORS.length];
      const pts = s.points.map((p) => [x(new Date(p.date + 'T00:00:00').getTime()), y(p.price)]);
      if (pts.length > 1) svg.append(svgEl('polyline', { points: pts.map((p) => p.join(',')).join(' '), fill: 'none', stroke: color, 'stroke-width': 2.2, 'stroke-linejoin': 'round' }));
      for (const [px, py] of pts) svg.append(svgEl('circle', { cx: px, cy: py, r: 3.2, fill: color }));
    });
    return svg;
  }

  // ---- form dialog ----
  // fields: [{key, label, value, type:'text'|'select', options:[{value,label}], placeholder, maxlength}]
  // onSubmit(values) may throw; its message is shown. conflictAction: {label, run(conflictId)} adds a
  // button when the server answers 409 with a conflict_id.
  function formDialog({ title, body, fields = [], submitLabel = 'Save', danger = false, onSubmit, conflictAction }) {
    return new Promise((resolve) => {
      const inputs = {};
      const error = h('p', { class: 'form-error', role: 'alert', hidden: true });
      const extra = h('div', { class: 'row', style: 'margin-top:8px' });
      const rows = fields.map((f) => {
        let control;
        if (f.type === 'select') {
          control = h('select', { class: 'select' }, f.options.map((o) => h('option', { value: o.value }, o.label)));
          control.value = f.value ?? '';
        } else if (f.type === 'checkbox') {
          control = h('input', { type: 'checkbox', checked: !!f.value });
          inputs[f.key] = control;
          return h('label', { style: 'display:flex;align-items:flex-start;gap:10px;margin-bottom:10px;cursor:pointer' }, control,
            h('span', {}, h('span', { class: 'label', style: 'display:block' }, f.label), f.hint ? h('span', { class: 'field-hint', style: 'color:var(--muted)' }, f.hint) : null));
        } else if (f.type === 'textarea') {
          control = h('textarea', { class: 'input', rows: String(f.rows || 4), maxlength: String(f.maxlength || 1000), placeholder: f.placeholder || '', style: 'min-height:104px;resize:vertical;line-height:1.4' });
          control.value = f.value ?? '';
        } else {
          control = h('input', { class: 'input', type: f.type === 'date' ? 'date' : 'text', value: f.value ?? '', placeholder: f.placeholder || '', maxlength: f.maxlength || 255 });
        }
        inputs[f.key] = control;
        return h('label', { class: 'field', style: 'margin-bottom:10px' }, h('span', { class: 'label' }, f.label), control,
          f.hint ? h('span', { class: 'field-hint', style: 'color:var(--muted)' }, f.hint) : null);
      });
      const submit = h('button', { class: 'btn ' + (danger ? 'btn-danger' : 'btn-primary'), type: 'submit' }, submitLabel);
      const dlg = h('dialog', { class: 'formdlg' },
        h('form', { method: 'dialog' },
          h('div', { class: 'dialog-body' }, h('h2', {}, title), body ? h('p', {}, body) : null, ...rows, error, extra),
          h('div', { class: 'dialog-actions' },
            h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => dlg.close('cancel') }, 'Cancel'), submit)));
      let done = false;
      dlg.querySelector('form').addEventListener('submit', async (e) => {
        e.preventDefault();
        error.hidden = true; extra.replaceChildren();
        submit.disabled = true;
        const values = {};
        for (const [k, el] of Object.entries(inputs)) values[k] = el.type === 'checkbox' ? el.checked : el.value;
        try {
          await onSubmit(values);
          done = true; dlg.close('ok');
        } catch (err) {
          error.textContent = err.message; error.hidden = false;
          const id = err.data && err.data.conflict_id;
          if (id && conflictAction) {
            extra.append(h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
              try { await conflictAction.run(id); done = true; dlg.close('ok'); } catch (e2) { error.textContent = e2.message; }
            } }, conflictAction.label));
          }
        } finally { submit.disabled = false; }
      });
      dlg.addEventListener('close', () => { dlg.remove(); resolve(done); });
      document.body.append(dlg); dlg.showModal();
      const first = dlg.querySelector('input, select'); if (first) first.focus();
    });
  }

  // Follow a background web lookup until it finishes; onTick gets {done, total, message, ...} each time.
  async function followJob(id, onTick) {
    for (;;) {
      const job = await api('api/v1/webinfo/jobs/' + encodeURIComponent(id));
      if (onTick) onTick(job);
      if (job.finished) return job;
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }

  // If this page was fetched a while ago and the app has since been updated — or a cache
  // served up something stale despite the no-store headers, which some embedded webviews are
  // known to do anyway — the version baked into the page (BAKED_VERSION, set by the
  // server when it sent the page) will not match what the server is actually running now. That
  // mismatch is exactly what causes errors like "RPI.someNewFunction is not a function": the
  // page's own HTML is current but app.js underneath it is an old copy, or vice versa. Reloading
  // fetches both fresh and clears it up without anyone needing to know to clear a cache by hand.
  // Guarded so a reload that somehow still doesn't fetch anything newer can't loop.
  async function checkVersion() {
    const baked = BAKED_VERSION;
    if (!baked) return; // a page from before this existed: nothing to compare against
    try {
      const info = await api('api/info');
      const key = 'rpi:reloaded-for:' + info.version;
      if (info.version && info.version !== baked && !sessionStorage.getItem(key)) {
        sessionStorage.setItem(key, '1');
        location.reload();
      }
    } catch (_) { /* offline or briefly unreachable: nothing to act on right now */ }
  }
  checkVersion();

  buildShell();
  me().then(fillShell).catch(() => { if (userName) userName.textContent = '—'; });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', backNav); else setTimeout(backNav, 0);

  window.RPI = {
    url, api, h, icon, money, fmtDate, relTime, parseNumber, fixed2, trimNum,
    me, setCurrentHome, pickHome, nav, go, adminTabs, homeContext, homeSwitcher, attachCombobox, sparkline, lineChart, formDialog,
    CHART_COLORS, followJob,
    statusPill, parseExtraction, itemMathIssue, reconcile, isNum, toast, confirmDialog,
  };
})();
