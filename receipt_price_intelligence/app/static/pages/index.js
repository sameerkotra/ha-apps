// index.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, money, fmtDate, relTime, parseExtraction, statusPill, toast, confirmDialog } = RPI;
    const $ = (id) => document.getElementById(id);

    const TABS = [
      { id: 'review', label: 'To review', match: (d) => ['PROCESSING', 'NEEDS_REVIEW', 'EXTRACTION_FAILED'].includes(d.status) },
      { id: 'saved', label: 'Saved', match: (d) => d.status === 'APPROVED' },
    ];
    // The list holds one line per receipt (api/v1/receipt-drafts/summaries): the ones to review all, saved ones a page
    // at a time ("Show more"). While some are being read, only their status is asked for every few seconds.
    // The tab is in the address (#review / #saved), so a reload or Back keeps it; with no address and nothing to
    // review, Saved opens.
    const hashTab = () => (TABS.some((t) => '#' + t.id === location.hash) ? location.hash.slice(1) : null);
    let me = null, homes = [], homeId = null, drafts = [], tab = hashTab() || 'review', timer = null, tagFilter = '', storeQuery = '';
    let tabChosen = !!hashTab();
    function setTab(id) {
      tab = id; tabChosen = true;
      if (window.BackNav) BackNav.replace('#' + id); else history.replaceState(history.state, '', '#' + id);
      render();
    }
    window.addEventListener('hashchange', () => { const t = hashTab(); if (t && t !== tab) { tab = t; tabChosen = true; render(); } });
    const search = h('input', { class: 'input', type: 'search', placeholder: 'Search stores', 'aria-label': 'Search saved receipts by store',
      oninput: () => { storeQuery = search.value.trim().toLowerCase(); render(); } });
    $('searchBox').append(icon('search'), search);
    let review = [], saved = [], savedMore = false, loadingMore = false;
    const SAVED_PAGE = 100;

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
        return h('button', { type: 'button', 'aria-pressed': String(tab === t.id), 'aria-current': tab === t.id ? 'true' : null, onclick: () => setTab(t.id) },
          t.label, t.id === 'review' && n ? h('span', { class: 'count' }, n) : null);
      }));
    }

    const tagsOf = (d) => (Array.isArray(d.tags) ? d.tags : []);
    const totalOf = (d) => (d.grand_total != null ? d.grand_total : (d.items_total || 0));

    function card(d) {
      const cur = d.currency || 'USD';
      const processing = d.status === 'PROCESSING';
      const failed = d.status === 'EXTRACTION_FAILED';
      const name = processing ? 'Reading receipt…' : failed ? 'Couldn’t read receipt' : (d.store_name || 'Unnamed store');
      const total = totalOf(d);
      const bits = [d.purchase_date ? fmtDate(d.purchase_date) : relTime(d.created_at)];
      if (!processing && !failed) bits.push(`${d.item_count || 0} items`);
      if (!processing && !failed && tagsOf(d).length) bits.push(tagsOf(d).map((t) => '#' + t).join(' '));
      return h('a', { class: 'receipt', href: 'review.html?id=' + encodeURIComponent(d.id) },
        h('div', { class: 'avatar' }, processing ? h('div', { class: 'spinner sm' }) : (name[0] || '?').toUpperCase()),
        h('div', { class: 'main' }, h('div', { class: 'name' }, name), h('div', { class: 'meta' }, bits.join(' · '))),
        h('div', { class: 'end' }, processing || failed ? null : h('div', { class: 'amount' }, money(total, cur)), statusPill(d.status, d.editing_saved)));
    }

    function empty(title, text, action) {
      return h('div', { class: 'empty' }, icon('receipt'), h('h2', {}, title), h('p', {}, text), action || null);
    }

    // Saved receipts by month (of the purchase), newest first: a sticky header with the month's count and total.
    const monthKey = (d) => String(d.purchase_date || d.created_at || '').slice(0, 7);
    function monthLabel(key) {
      const m = /^(\d{4})-(\d{2})$/.exec(key);
      return m ? new Date(+m[1], +m[2] - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : 'No date';
    }
    function byMonth(list) {
      const groups = new Map();
      for (const d of list) { const k = monthKey(d); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(d); }
      const out = [];
      for (const k of [...groups.keys()].sort().reverse()) {
        const g = groups.get(k);
        const total = g.reduce((s, d) => s + (d.grand_total || 0), 0);
        out.push(h('div', { class: 'ui-group-head' }, `${monthLabel(k)} · ${g.length} receipt${g.length === 1 ? '' : 's'}`,
          h('span', { class: 'ui-group-total' }, money(total))), ...g.map(card));
      }
      return out;
    }

    function render() {
      renderFilters();
      const c = $('content'), stat = $('stat');
      stat.textContent = '';
      const t = TABS.find((x) => x.id === tab);
      let shown = drafts.filter(t.match);
      // Saved receipts can be narrowed to one tag (business, warranty, ...)
      const tagsInUse = [...new Set(drafts.filter((d) => d.status === 'APPROVED').flatMap(tagsOf))].sort();
      $('tagFilters').replaceChildren(...(tab === 'saved' && tagsInUse.length ? [h('button', { type: 'button', class: 'chip-btn', 'aria-pressed': String(!tagFilter), onclick: () => { tagFilter = ''; render(); } }, 'All'),
        ...tagsInUse.map((tg) => h('button', { type: 'button', class: 'chip-btn', 'aria-pressed': String(tagFilter === tg), onclick: () => { tagFilter = tagFilter === tg ? '' : tg; render(); } }, '#' + tg))] : []));
      if (tab === 'saved' && tagFilter) shown = shown.filter((d) => tagsOf(d).includes(tagFilter));
      $('searchBox').hidden = tab !== 'saved' || !drafts.some((d) => d.status === 'APPROVED');
      if (tab === 'saved' && storeQuery) shown = shown.filter((d) => (d.store_name || '').toLowerCase().includes(storeQuery));
      if (tab === 'saved' && shown.length) {
        const sum = shown.reduce((s, d) => s + (d.grand_total || 0), 0);
        stat.textContent = `${shown.length}${savedMore ? '+' : ''} receipts · ${money(sum)}${savedMore ? ' (shown so far)' : ''}`;
      }
      if (!shown.length) {
        const msg = tab === 'review' ? ['Nothing to review', 'Scan a receipt and it will show up here for a quick check.']
          : storeQuery || tagFilter ? ['No receipts match', 'Try another store name or tag.']
          : ['No saved receipts yet', 'Approved receipts appear here.'];
        return c.replaceChildren(empty(msg[0], msg[1], h('a', { class: 'btn btn-primary', href: 'capture.html' }, icon('camera'), 'Scan a receipt')));
      }
      const more = tab === 'saved' && savedMore
        ? h('button', { class: 'btn btn-sm', type: 'button', disabled: loadingMore ? true : null, onclick: loadMoreSaved }, loadingMore ? 'Loading…' : 'Show older receipts')
        : null;
      c.replaceChildren(...(tab === 'saved' ? byMonth(shown) : shown.map(card)), ...(more ? [h('div', { style: 'text-align:center;margin:12px 0' }, more)] : []));
    }

    const summaries = (group, after) => api(`api/v1/receipt-drafts/summaries?home_id=${encodeURIComponent(homeId)}&group=${group}`
      + `&limit=${group === 'saved' ? SAVED_PAGE : 500}`
      + (after ? `&before=${encodeURIComponent(after.created_at)}&before_id=${encodeURIComponent(after.id)}` : ''));

    async function loadMoreSaved() {
      if (!saved.length) return;
      loadingMore = true; render();
      try {
        const page = await summaries('saved', saved[saved.length - 1]);
        saved = saved.concat(page.drafts); savedMore = page.more; drafts = review.concat(saved);
      } catch (e) { toast(e.message, 'error'); }
      loadingMore = false; render();
    }

    // Only the receipts being read are asked about; the list is fetched again once one of them is done.
    function pollReading() {
      clearTimeout(timer);
      const reading = review.filter((d) => d.status === 'PROCESSING').map((d) => d.id);
      if (!reading.length) return;
      timer = setTimeout(async () => {
        try {
          const states = await api('api/v1/extraction/status?ids=' + encodeURIComponent(reading.slice(0, 100).join(',')));
          if (states.some((x) => x.status !== 'PROCESSING') || states.length < Math.min(reading.length, 100)) return load(false);
        } catch (_) { /* try again next time */ }
        pollReading();
      }, 4000);
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
        const [r, s] = await Promise.all([summaries('review'), summaries('saved')]);
        review = r.drafts; saved = s.drafts; savedMore = s.more;
        drafts = review.concat(saved);
        if (!tabChosen && tab === 'review' && !review.length && saved.length) tab = 'saved';
        render();
        pollReading();
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
      // which folder and mail server: administrators only (members see "folder" / "mailbox")
      const sources = [st.folder_ready ? (st.folder ? `folder ${st.folder}` : 'folder') : null,
        st.mailbox_ready ? (st.mailbox ? `mailbox ${st.mailbox}` : 'mailbox') : null].filter(Boolean);
      if (!sources.length) return box.replaceChildren();
      const btn = h('button', { type: 'button' }, icon('refresh'), 'Check now');
      const menu = h('details', { class: 'menu' }, h('summary', { class: 'icon-btn', 'aria-label': 'Import options', title: 'Import options' }, icon('more')),
        h('div', { class: 'menu-list', style: 'bottom:calc(100% + 6px);top:auto' }, btn));
      btn.addEventListener('click', async () => {
        menu.open = false;
        btn.disabled = true;
        try {
          const r = await api('api/v1/import/scan', { method: 'POST', body: {} });
          toast(r.imported ? `Imported ${r.imported} receipt${r.imported === 1 ? '' : 's'}. They are being read.` : 'Nothing new to import', r.imported ? 'success' : undefined);
          if (r.imported) load(true);
        } catch (e) { toast(e.message, 'error'); }
        btn.disabled = false;
      });
      box.replaceChildren(h('div', { class: 'importrow' }, icon('download'),
        h('span', { class: 'grow', title: 'New ones appear under To review.' }, `Imported from your ${sources.join(' and ')}`), menu));
    }

    init();
