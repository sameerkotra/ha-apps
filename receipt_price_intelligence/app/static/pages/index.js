// index.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, money, fmtDate, relTime, parseExtraction, statusPill, toast, confirmDialog } = RPI;
    const $ = (id) => document.getElementById(id);

    const TABS = [
      { id: 'review', label: 'To review', match: (d) => ['PROCESSING', 'NEEDS_REVIEW', 'EXTRACTION_FAILED'].includes(d.status) },
      { id: 'saved', label: 'Saved', match: (d) => d.status === 'APPROVED' },
    ];
    let me = null, homes = [], homeId = null, drafts = [], tab = 'review', timer = null, tagFilter = '';

    for (const id of ['scanTop', 'scanFab']) $(id).append(icon('camera'), 'Scan receipt');
    $('bulkBtn').append(icon('upload'));

    const homeName = () => (homes.find((x) => x.id === homeId) || {}).name || '';

    function renderHeader() {
      $('homeLine').textContent = homeName() ? `${homeName()} · price history from your shopping trips` : 'Price history from your shopping trips';
      const sw = $('homeSwitch');
      if (homes.length > 1) {
        const sel = h('select', { class: 'select home-select', 'aria-label': 'Home' }, homes.map((x) => h('option', { value: x.id }, x.name)));
        sel.value = homeId;
        sel.addEventListener('change', () => { homeId = sel.value; RPI.setCurrentHome(homeId); renderHeader(); load(true); });
        sw.replaceChildren(sel);
      } else sw.replaceChildren();
    }

    function renderFilters() {
      $('filters').replaceChildren(...TABS.map((t) => {
        const n = t.match ? drafts.filter(t.match).length : 0;
        return h('button', { type: 'button', 'aria-pressed': String(tab === t.id), onclick: () => { tab = t.id; render(); } },
          t.label, t.id === 'review' && n ? h('span', { class: 'count' }, n) : null);
      }));
    }

    function card(d) {
      const x = parseExtraction(d);
      const cur = x.currency || 'USD';
      const processing = d.status === 'PROCESSING';
      const failed = d.status === 'EXTRACTION_FAILED';
      const name = processing ? 'Reading receipt…' : failed ? 'Couldn’t read receipt' : (x.store_name || 'Unnamed store');
      const total = x.grand_total != null ? x.grand_total : (d.items || []).reduce((s, i) => s + (i.line_total || 0), 0);
      const bits = [x.purchase_date ? fmtDate(x.purchase_date) : relTime(d.created_at)];
      if (!processing && !failed) bits.push(`${(d.items || []).length} items`);
      if (!processing && !failed && x.tags && x.tags.length) bits.push(x.tags.map((t) => '#' + t).join(' '));
      return h('a', { class: 'receipt', href: 'review.html?id=' + encodeURIComponent(d.id) },
        h('div', { class: 'avatar' }, processing ? h('div', { class: 'spinner sm' }) : (name[0] || '?').toUpperCase()),
        h('div', { class: 'main' }, h('div', { class: 'name' }, name), h('div', { class: 'meta' }, bits.join(' · '))),
        h('div', { class: 'end' }, processing || failed ? null : h('div', { class: 'amount' }, money(total, cur)), statusPill(d.status, d.editing_saved)));
    }

    function empty(title, text, action) {
      return h('div', { class: 'empty' }, icon('receipt'), h('h2', {}, title), h('p', {}, text), action || null);
    }

    function render() {
      renderFilters();
      const c = $('content'), stat = $('stat');
      stat.textContent = '';
      const t = TABS.find((x) => x.id === tab);
      let shown = drafts.filter(t.match);
      // Saved receipts can be narrowed to one tag (business, warranty, ...)
      const tagsInUse = [...new Set(drafts.filter((d) => d.status === 'APPROVED').flatMap((d) => parseExtraction(d).tags || []))].sort();
      $('tagFilters').replaceChildren(...(tab === 'saved' && tagsInUse.length ? [h('button', { type: 'button', class: 'chip-btn', 'aria-pressed': String(!tagFilter), onclick: () => { tagFilter = ''; render(); } }, 'All'),
        ...tagsInUse.map((tg) => h('button', { type: 'button', class: 'chip-btn', 'aria-pressed': String(tagFilter === tg), onclick: () => { tagFilter = tagFilter === tg ? '' : tg; render(); } }, '#' + tg))] : []));
      if (tab === 'saved' && tagFilter) shown = shown.filter((d) => (parseExtraction(d).tags || []).includes(tagFilter));
      if (tab === 'saved' && shown.length) {
        const sum = shown.reduce((s, d) => s + (parseExtraction(d).grand_total || 0), 0);
        stat.textContent = `${shown.length} receipts · ${money(sum)}`;
      }
      if (!shown.length) {
        const msg = tab === 'review' ? ['Nothing to review', 'Scan a receipt and it will show up here for a quick check.']
          : ['No saved receipts yet', 'Approved receipts appear here.'];
        return c.replaceChildren(empty(msg[0], msg[1], h('a', { class: 'btn btn-primary', href: 'capture.html' }, icon('camera'), 'Scan a receipt')));
      }
      c.replaceChildren(...shown.map(card));
    }

    // ---------- loading ----------
    function onboarding() {
      $('toolbar').hidden = true; $('scanFab').hidden = true;
      const c = $('content');
      if (me.is_admin) {
        const name = h('input', { class: 'input', placeholder: 'e.g. Our house', maxlength: '255', 'aria-label': 'Home name' });
        const go = async () => {
          try { const created = await api('api/v1/homes', { method: 'POST', body: { name: name.value } }); RPI.setCurrentHome(created.id); init(); }
          catch (e) { toast(e.message, 'error'); }
        };
        name.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
        c.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body stack' },
          h('div', {}, h('h2', { style: 'font-size:1.15rem' }, 'Create your home'),
            h('p', { class: 'muted' }, 'Receipts and price history are kept per home. Everyone using Home Assistant can add receipts to it.')),
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'Name'), name),
          h('button', { class: 'btn btn-primary', onclick: go }, icon('home'), 'Create home'))));
      } else {
        c.replaceChildren(empty('No home yet', 'An administrator needs to create a home before receipts can be added.'));
      }
    }

    async function load(first) {
      const c = $('content');
      if (first) c.replaceChildren(...[0, 1, 2].map(() => h('div', { class: 'skeleton', style: 'height:72px' })));
      try {
        drafts = await api('api/v1/receipt-drafts?home_id=' + encodeURIComponent(homeId));
        render();
        clearTimeout(timer);
        if (drafts.some((d) => d.status === 'PROCESSING')) timer = setTimeout(() => load(false), 4000);
      } catch (e) {
        if (!first) return;
        c.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'),
          h('div', { class: 'banner-body' }, h('strong', {}, 'Couldn’t load receipts. '), h('span', { class: 'banner-text' }, e.message)),
          h('button', { class: 'btn btn-sm', onclick: () => load(true) }, 'Retry')));
      }
    }

    async function init() {
      try {
        [me, homes] = await Promise.all([RPI.me(), api('api/v1/homes')]);
      } catch (e) {
        return $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'),
          h('div', { class: 'banner-body' }, h('strong', {}, 'Couldn’t sign in. '), h('span', { class: 'banner-text' }, e.message))));
      }
      homeId = RPI.pickHome(homes);
      if (!homeId) return onboarding();
      $('toolbar').hidden = false; $('scanFab').hidden = false;
      renderHeader();
      load(true);
      importStatus();
    }

    // ---------- receipts arriving by folder or email ----------
    async function importStatus() {
      const box = $('importBox');
      let st;
      try { st = await api('api/v1/import/status'); } catch (_) { return box.replaceChildren(); }
      const sources = [st.folder_ready ? `folder ${st.folder}` : null, st.mailbox_ready ? `mailbox ${st.mailbox}` : null].filter(Boolean);
      if (!sources.length) return box.replaceChildren();
      const btn = h('button', { class: 'btn btn-sm', type: 'button' }, icon('refresh'), 'Check now');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const r = await api('api/v1/import/scan', { method: 'POST', body: {} });
          toast(r.imported ? `Imported ${r.imported} receipt${r.imported === 1 ? '' : 's'}. They are being read.` : 'Nothing new to import', r.imported ? 'success' : undefined);
          if (r.imported) load(true);
        } catch (e) { toast(e.message, 'error'); }
        btn.disabled = false;
      });
      box.replaceChildren(h('div', { class: 'banner', style: 'margin-bottom:12px' }, icon('download'),
        h('div', { class: 'banner-body' }, h('span', { class: 'banner-text' }, `Receipts are imported from your ${sources.join(' and ')}. New ones appear under To review.`)), btn));
    }

    init();
