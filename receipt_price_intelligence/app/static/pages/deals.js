// deals.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, money, toast } = RPI;
    const $ = (id) => document.getElementById(id);
    let ctx = null, snap = null, query = '', onlyCheaper = false, refreshing = false;
    let alertList = [], targets = [], pricedItems = [], webDeals = [], webPrices = [], webConfigured = false, webJobText = null, priceAllFreshNote = null, priceHistory = [];

    const url = (suffix = '') => `api/v1/homes/${encodeURIComponent(ctx.homeId)}/best-prices${suffix}`;

    const priceText = (p, unit) => (p == null ? '—' : money(p) + (unit && unit !== 'each' ? '/' + unit : ''));
    const fmtDay = (d) => new Date(d + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    const card = (...kids) => h('div', { class: 'card' }, ...kids);

    // ---------------------------------------------------------------- data
    let waysToSave = [], overcharged = [];
    // $3.29, or $3.199 for a price posted to a tenth of a cent (fuel)
    const cost = (p) => (p != null && Math.round(p * 100) !== Math.round(p * 1000) / 10 ? '$' + p.toFixed(3) : money(p));
    const per = (u) => (u && String(u).toLowerCase() !== 'each' ? '/' + String(u).toLowerCase() : '');

    async function load() {
      try { snap = await api(url()); } catch (e) {
        return $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message),
          h('button', { class: 'btn btn-sm', onclick: load }, 'Retry')));
      }
      const home = `api/v1/homes/${encodeURIComponent(ctx.homeId)}`;
      let webStatus, found;
      // all at once
      [alertList, targets, pricedItems, webStatus, webDeals, webPrices, priceHistory, found] = await Promise.all([
        api(`${home}/alerts`).catch(() => []), api(`${home}/price-targets`).catch(() => []), api(`${home}/planner/items?limit=1000`).catch(() => []),
        api('api/v1/webinfo/status').catch(() => ({ configured: false })), api(`${home}/webinfo/deals?kind=DEAL`).catch(() => []), api(`${home}/webinfo/deals?kind=PRICE`).catch(() => []), api(`${home}/webinfo/prices/history`).catch(() => []),
        api(`${home}/lookout-summary`).catch(() => null),
      ]);
      webConfigured = !!webStatus.configured;
      waysToSave = Array.isArray(found && found.savings) ? found.savings : [];
      overcharged = Array.isArray(found && found.overcharges) ? found.overcharges : [];
      render();
    }
    async function refresh() {
      if (refreshing) return;
      refreshing = true; render();
      try { snap = await api(url('/refresh'), { method: 'POST', body: {} }); toast('Prices checked', 'success'); }
      catch (e) { toast(e.message, 'error'); }
      refreshing = false; render();
    }

    // ---------------------------------------------------------------- render

    // ---------------------------------------------------------------- alerts and price targets
    const KIND = { PRICE_TARGET: ['Target reached', 'ok'], NEW_LOW: ['New low', 'ok'], SAVING: ['Cheaper store', 'ok'], PRICE_DROP: ['Price drop', 'ok'], WEB_DEAL: ['Deal online', 'ok'], RESTOCK: ['Restock', 'warn'] };
    function alertsSection() {
      if (!alertList.length) return [];
      return [h('div', { class: 'sect' }, 'Alerts'), ...alertList.map((a) => {
        const [label, kind] = KIND[a.kind] || [a.kind, ''];
        return card(h('div', { class: 'rec' },
          h('div', { class: 'row' }, h('span', { class: 'headline' }, a.title), h('span', { class: 'pill ' + kind }, label)),
          h('div', {}, a.message),
          h('div', { class: 'meta row-between' }, h('span', {}, RPI.relTime(a.created_at)),
            h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
              try { await api(`api/v1/alerts/${encodeURIComponent(a.id)}/dismiss`, { method: 'POST', body: {} }); alertList = alertList.filter((x) => x.id !== a.id); render(); }
              catch (e) { toast(e.message, 'error'); }
            } }, 'Dismiss'))));
      })];
    }

    // ---- deals found online through your local search server
    function webDealsSection() {
      if (!webConfigured && !webDeals.length) return [];
      const start = h('button', { class: 'btn btn-sm', type: 'button', disabled: webJobText != null, onclick: lookForDeals }, icon('refresh'), 'Look for deals');
      const rows = webDeals.map((d) => h('div', { class: 'srow' },
        h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, d.item_name || d.product),
          h('div', { class: 'meta' }, `${d.chain}${d.valid_to ? ' · until ' + fmtDay(d.valid_to) : ''}${d.conditions ? ' · ' + d.conditions : ''}`),
          h('div', { class: 'meta' }, d.usual_price != null ? `You usually pay ${priceText(d.usual_price, d.compare_unit)}` : 'You have no price for it there yet',
            d.source_url ? h('a', { href: d.source_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, d.source || 'source') : null)),
        d.saving_pct != null && d.saving_pct > 0 ? h('span', { class: 'pill ok' }, `${d.saving_pct.toFixed(0)}% less`) : null,
        h('div', { class: 'amt' }, priceText(d.price, d.unit))));
      return [h('div', { class: 'sect' }, 'Deals found online'),
        card(...(rows.length ? rows : [h('div', { class: 'srow' }, h('span', { class: 'meta' }, webJobText || 'No deals found yet. Look for deals at the stores you shop at. They are found by searching the web, so check the store’s own ad before you go.'))]),
          h('div', { class: 'srow' }, start, webJobText ? h('span', { class: 'meta' }, webJobText) : null))];
    }
    // ---- current shelf prices found on the stores' websites
    function webPricesSection() {
      if (!webConfigured && !webPrices.length) return [];
      const start = h('button', { class: 'btn btn-sm', type: 'button', disabled: webJobText != null, onclick: () => checkPrices(false) }, icon('refresh'), 'Check prices online');
      const rows = webPrices.map((d) => {
        const change = d.saving_pct == null ? null : -d.saving_pct; // positive = costs more than you last paid
        return h('div', { class: 'srow' },
          h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, d.item_name || d.product),
            h('div', { class: 'meta' }, `${d.chain} · ${d.product}${d.conditions ? ' · ' + d.conditions : ''}`),
            h('div', { class: 'meta' }, `Checked ${checkedAt(d.fetched_at)} · `, methodLabel(d.method)),
            h('div', { class: 'meta' }, d.usual_price != null ? `You last paid ${priceText(d.usual_price, d.compare_unit)}` : 'You have no price for it there yet',
              d.source_url ? h('a', { href: d.source_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, d.source || 'source') : null)),
          change != null && Math.abs(change) >= 1 ? h('span', { class: 'pill ' + (change > 0 ? 'danger' : 'ok') }, `${change > 0 ? '+' : '−'}${Math.abs(change).toFixed(0)}%`) : null,
          h('div', { class: 'amt' }, priceText(d.price, d.unit)));
      });
      return [h('div', { class: 'sect' }, 'Latest prices online'),
        card(...(rows.length ? rows : [h('div', { class: 'srow' }, h('span', { class: 'meta' }, webJobText || 'Check what your most bought items cost now on the websites of the stores you buy them at. Online prices can differ from the shelf, so use them as a guide.'))]),
          h('div', { class: 'srow' }, start, webJobText ? h('span', { class: 'meta' }, webJobText) : null),
          priceAllFreshNote ? h('div', { class: 'srow' }, h('span', { class: 'meta' }, priceAllFreshNote),
            h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => checkPrices(true) }, 'Check again anyway')) : null)];
    }
    // How a price was read, in words: the page's own product data is the most reliable.
    const METHODS = { structured: ['product data', 'ok', 'Read from the product information the store publishes on the page'],
      text: ['page text', '', 'Read from a price shown next to the product name on the page'],
      model: ['read by model', 'warn', 'Nothing could be read directly, so the model read the page; double-check this one'] };
    function methodLabel(m) {
      const [label, kind, title] = METHODS[m] || METHODS.model;
      return h('span', { class: 'pill ' + kind, title, style: 'font-size:.7rem;padding:1px 7px' }, label);
    }
    function checkedAt(iso) {
      if (!iso) return 'at an unknown time';
      const d = new Date(iso);
      return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    }
    function dayTitle(day) {
      if (day === 'unknown') return 'Unknown day';
      const d = new Date(day + 'T00:00:00'), today = new Date(); today.setHours(0, 0, 0, 0);
      const diff = Math.round((today - d) / 86400000);
      return diff === 0 ? 'Today' : diff === 1 ? 'Yesterday' : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
    }

    // Every online price check, grouped by the day it was made.
    function priceHistorySection() {
      if (!priceHistory.length) return [];
      const total = priceHistory.reduce((n, g) => n + g.checks.length, 0);
      // The server groups by UTC date; regroup by the local day each check was made, so the heading
      // matches the time shown on each check (newest first, as the server sends them).
      const localDay = (iso) => { const d = new Date(iso); return isNaN(d) ? 'unknown' : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
      const groups = [];
      for (const c of priceHistory.flatMap((g) => g.checks)) {
        const day = localDay(c.time);
        if (!groups.length || groups[groups.length - 1].date !== day) groups.push({ date: day, checks: [] });
        groups[groups.length - 1].checks.push(c);
      }
      return [h('details', { class: 'card', style: 'margin-top:4px' },
        h('summary', { class: 'srow', style: 'cursor:pointer;font-weight:650' }, `Online price checks by date (${total})`),
        ...groups.map((g) => h('div', {},
          h('div', { class: 'srow', style: 'background:var(--surface-2);font-weight:650;font-size:.85rem' }, `${dayTitle(g.date)} · ${g.checks.length} check${g.checks.length === 1 ? '' : 's'}`),
          ...g.checks.map((c) => h('div', { class: 'srow' },
            h('div', { class: 'grow' },
              h('div', { style: 'font-weight:600' }, `${c.item_name} at ${c.chain || 'a store'}`),
              h('div', { class: 'meta' }, `${new Date(c.time).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })} · ${c.product}`),
              h('div', { class: 'meta' }, methodLabel(c.method),
                c.source_url ? h('a', { href: c.source_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, c.source || 'source') : null)),
            c.latest ? h('span', { class: 'pill ok', title: 'The price currently used for this item at this store' }, 'current') : null,
            h('div', { class: 'amt' }, priceText(c.price, c.unit)))))))];
    }

    async function checkPrices(force) {
      priceAllFreshNote = null;
      try {
        const job = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/webinfo/prices/refresh`, { method: 'POST', body: { force } });
        webJobText = job.skipped ? `Starting… (${job.skipped} already up to date, skipped)` : 'Starting…'; render();
        const done = await RPI.followJob(job.id, (j) => { webJobText = j.skipped ? `${j.message} (${j.done} of ${j.total}; ${j.skipped} already up to date)` : `${j.message} (${j.done} of ${j.total})`; render(); });
        webJobText = null;
        const skippedNote = done.skipped ? ` ${done.skipped} item${done.skipped === 1 ? '' : 's'} were already checked recently and left as they were.` : '';
        toast(done.error ? done.error : done.results ? `Found ${done.results} current price${done.results === 1 ? '' : 's'}.${skippedNote}` : `No prices could be found online.${skippedNote}`, done.error || !done.results ? 'error' : 'success');
        await load();
      } catch (e) {
        webJobText = null;
        if (/already checked online within the last/.test(e.message)) { priceAllFreshNote = e.message; render(); }
        else { toast(e.message, 'error'); render(); }
      }
    }

    async function lookForDeals() {
      try {
        const job = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/webinfo/deals/refresh`, { method: 'POST', body: {} });
        webJobText = 'Starting…'; render();
        const done = await RPI.followJob(job.id, (j) => { webJobText = `${j.message} (${j.done} of ${j.total})`; render(); });
        webJobText = null;
        toast(done.error ? done.error : done.results ? `Found ${done.results} deal${done.results === 1 ? '' : 's'} on your items` : 'No deals found on your items', done.error || !done.results ? 'error' : 'success');
        await load();
      } catch (e) { webJobText = null; toast(e.message, 'error'); render(); }
    }

    function targetText(t) {
      return t.mode === 'NEW_LOW' ? 'Tell me about a new low price' : `Tell me at or below ${priceText(t.target_price, t.unit)}`;
    }
    function targetsSection() {
      const rows = targets.map((t) => {
        const reached = t.current && t.mode === 'BELOW' && t.current.price <= t.target_price + 1e-9;
        return h('div', { class: 'srow' },
          h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, t.item_name), h('div', { class: 'meta' }, targetText(t)),
            t.current ? h('div', { class: 'meta' }, `Now ${priceText(t.current.price, t.current.unit)} at ${t.current.store} (${fmtDay(t.current.date)})`) : h('div', { class: 'meta' }, 'No price seen in the last 30 days')),
          reached ? h('span', { class: 'pill ok' }, 'Reached') : null,
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Remove target for ${t.item_name}`, onclick: async () => {
            try { await api(`api/v1/price-targets/${encodeURIComponent(t.id)}`, { method: 'DELETE' }); targets = targets.filter((x) => x.id !== t.id); render(); }
            catch (e) { toast(e.message, 'error'); }
          } }, icon('trash')));
      });
      return [h('div', { class: 'sect' }, 'Price targets'),
        card(...(rows.length ? rows : [h('div', { class: 'srow' }, h('span', { class: 'meta' }, 'Get told when an item drops to a price you choose, or hits a new low. Alerts appear here and in Home Assistant.'))]),
          h('div', { class: 'srow' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: () => targetDialog(null), disabled: !pricedItems.length }, icon('plus'), 'Add a price target')))];
    }

    function targetDialog(preselect) {
      let chosen = preselect ? pricedItems.find((i) => i.id === preselect) || null : null;
      const item = h('input', { class: 'input', type: 'text', placeholder: 'Search your items', value: chosen ? chosen.name : '', 'aria-label': 'Item' });
      RPI.attachCombobox(item, { options: () => pricedItems.map((i) => ({ label: i.name, hint: i.unit !== 'each' ? `per ${i.unit}` : '', id: i.id })), allowCreate: false,
        onPick: (opt) => { chosen = pricedItems.find((i) => i.id === opt.id); paint(); } });
      item.addEventListener('input', () => { if (chosen && item.value !== chosen.name) { chosen = null; paint(); } });
      const mode = h('select', { class: 'select' }, h('option', { value: 'BELOW' }, 'At or below a price'), h('option', { value: 'NEW_LOW' }, 'A new lowest price'));
      const price = h('input', { class: 'input', type: 'number', min: '0', step: 'any', inputmode: 'decimal', placeholder: '3.00' });
      const priceLabel = h('span', { class: 'label' });
      const priceField = h('label', { class: 'field' }, priceLabel, price);
      const error = h('p', { class: 'form-error', role: 'alert', hidden: true });
      function paint() {
        priceField.hidden = mode.value !== 'BELOW';
        priceLabel.textContent = `Price${chosen && chosen.unit !== 'each' ? ` (per ${chosen.unit})` : ''}`;
      }
      mode.addEventListener('change', paint); paint();
      const save = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Add target');
      const dlg = h('dialog', { class: 'formdlg' }, h('form', { method: 'dialog' },
        h('div', { class: 'dialog-body' }, h('h2', {}, 'Add a price target'),
          h('label', { class: 'field' }, h('span', { class: 'label' }, 'Item'), item), h('label', { class: 'field' }, h('span', { class: 'label' }, 'Tell me when it is'), mode), priceField, error),
        h('div', { class: 'dialog-actions' }, h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => dlg.close() }, 'Cancel'), save)));
      dlg.querySelector('form').addEventListener('submit', async (e) => {
        e.preventDefault(); error.hidden = true;
        if (!chosen) { error.textContent = 'Choose an item from the list'; error.hidden = false; return; }
        save.disabled = true;
        try {
          await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/price-targets`, { method: 'POST', body: { item_id: chosen.id, mode: mode.value, target_price: mode.value === 'BELOW' ? price.value : null } });
          dlg.close(); toast('Price target added', 'success'); await load();
        } catch (err) { error.textContent = err.message; error.hidden = false; }
        save.disabled = false;
      });
      dlg.addEventListener('close', () => dlg.remove());
      document.body.append(dlg); dlg.showModal(); item.focus();
    }

    // ---------------------------------------------------------------- layout: a short header, then one tab at a time
    let tab = localStorage.getItem('deals:tab') || 'foryou', showAllFeed = false;
    const TABS = [['foryou', 'For you'], ['items', 'All items'], ['online', 'Online'], ['alerts', 'Alerts']];

    // One list of the best things to act on, from every source; each item once, biggest saving first.
    function feed() {
      const out = [], seen = new Set();
      const push = (x) => { if (x.item && seen.has(x.item) && x.kind !== 'refund') return; seen.add(x.item); out.push(x); };
      for (const o of overcharged) push({ kind: 'refund', item: o.item, rank: 1000 + o.extra, pill: ['Ask for a refund', 'warn'],
        title: `${o.item} at ${o.store}`, line: `Paid ${cost(o.paid)}${per(o.unit)} on ${fmtDay(o.date)}; the store posted ${cost(o.posted)}`, value: `+${money(o.extra)}`, url: o.source_url });
      for (const r of snap && snap.switch_store || []) push({ kind: 'switch', item: r.item_name, rank: 500 + (r.est_monthly_savings || 0) * 10 + r.saving_pct, pill: ['Cheaper store', 'ok'],
        title: r.item_name, line: `${r.to.label.split(' · ')[0]} ${priceText(r.to.price, r.unit)} instead of ${r.from.label.split(' · ')[0]} ${priceText(r.from.price, r.unit)}`,
        value: r.est_monthly_savings ? `${money(r.est_monthly_savings)}/mo` : `${r.saving_pct}% less` });
      for (const w of waysToSave) push({ kind: w.kind, item: w.item, rank: 300 + w.percent, pill: [w.kind === 'pack' ? 'Bigger pack' : 'Cheaper store', 'ok'],
        title: w.item, line: w.kind === 'store'
          ? `${w.better.store}${w.better.size ? ' (' + w.better.size + ')' : ''} ${cost(w.better.price)}${per(w.unit)} instead of ${w.now.store} ${cost(w.now.price)}${per(w.unit)}`
          : `The ${w.better.size} size: ${cost(w.better.price)}${per(w.unit)} instead of ${cost(w.now.price)}${per(w.unit)} (${w.now.size})`,
        value: `${w.percent}% less` });
      for (const d of webDeals) if (d.saving_pct > 0) push({ kind: 'deal', item: d.item_name || d.product, rank: 200 + d.saving_pct, pill: ['Deal', 'ok'],
        title: d.item_name || d.product, line: `${priceText(d.price, d.unit)} at ${d.chain}${d.valid_to ? ' until ' + fmtDay(d.valid_to) : ''}`, value: `${d.saving_pct.toFixed(0)}% less`, url: d.source_url });
      for (const a of snap && snap.price_alerts || []) if (a.type !== 'PRICE_UP') push({ kind: 'drop', item: a.item_name, rank: 100 + Math.abs(a.change_pct), pill: ['Price drop', 'ok'],
        title: a.item_name, line: `${priceText(a.price, a.unit)} at ${a.store}, usually ${priceText(a.usual_price, a.unit)}`, value: `↓ ${Math.abs(a.change_pct).toFixed(0)}%` });
      return out.sort((a, b) => b.rank - a.rank);
    }
    function feedRow(x) {
      return h('div', { class: 'frow' },
        h('div', { class: 'grow' },
          h('div', { class: 'ftop' }, h('span', { class: 'fname' }, x.title), h('span', { class: 'pill ' + x.pill[1] }, x.pill[0])),
          h('div', { class: 'meta' }, x.line, x.url ? [' · ', h('a', { href: x.url, target: '_blank', rel: 'noopener' }, 'page')] : null)),
        h('div', { class: 'fval' }, x.value));
    }
    function forYouTab() {
      const items = feed();
      if (!items.length) {
        return [h('div', { class: 'empty' }, icon('tag'), h('h2', {}, 'Nothing to act on right now'),
          h('p', {}, snap && snap.summary ? 'You already buy things where they are cheapest. New receipts and online prices keep this up to date.'
            : 'Approve a few receipts, and check prices, to see where you could save.'))];
      }
      const shown = showAllFeed ? items : items.slice(0, 8);
      return [card(...shown.map(feedRow),
        items.length > shown.length ? h('div', { class: 'srow' }, h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => { showAllFeed = true; render(); } }, `Show ${items.length - shown.length} more`)) : null)];
    }
    function itemsTab() {
      if (!snap || !snap.summary || !snap.summary.items_checked) return emptyState();
      const search = h('input', { class: 'input', type: 'search', placeholder: 'Search items', value: query, 'aria-label': 'Search items', autocomplete: 'off' });
      search.addEventListener('input', () => { query = search.value; paintList(); });
      const only = h('input', { type: 'checkbox', checked: onlyCheaper });
      only.addEventListener('change', () => { onlyCheaper = only.checked; paintList(); });
      return [h('div', { class: 'controls' }, h('div', { class: 'search' }, icon('search'), search), h('label', { class: 'check' }, only, 'Only items with a cheaper option')),
        h('div', { class: 'list', id: 'list' }),
        h('p', { class: 'note' }, `From what you paid in the last ${snap.window_days} days. Items are compared in the same unit, when bought at two or more stores. Tap one for every store's price.`)];
    }
    function onlineTab() {
      const parts = [...webDealsSection(), ...webPricesSection(), ...priceHistorySection()];
      return parts.length ? parts : [h('div', { class: 'empty' }, icon('search'), h('h2', {}, 'Online prices are off'),
        h('p', {}, 'Set up web search in Admin → App settings (Tavily, Parallel or SearXNG) to check prices and deals online.'))];
    }
    function alertsTab() {
      const moved = (snap && snap.price_alerts || []).map((a) => {
        const up = a.type === 'PRICE_UP';
        return h('div', { class: 'srow' }, h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, a.item_name),
          h('div', { class: 'meta' }, `${priceText(a.price, a.unit)} at ${a.store}, usually ${priceText(a.usual_price, a.unit)} · ${fmtDay(a.date)}`)),
          h('span', { class: 'pill ' + (up ? 'danger' : 'ok') }, (up ? '↑ +' : '↓ ') + Math.abs(a.change_pct).toFixed(0) + '%'));
      });
      return [...alertsSection(), ...(moved.length ? [h('div', { class: 'sect' }, 'Prices that moved'), card(...moved)] : []), ...targetsSection()];
    }
    function emptyState() {
      if (!snap || !snap.summary) return [h('div', { class: 'empty' }, icon('tag'), h('h2', {}, snap && snap.status === 'FAILED' ? 'No results yet' : 'Nothing checked yet'),
        h('p', {}, 'Prices are checked once a day. Use “Check now” to see results straight away.'))];
      return [h('div', { class: 'empty' }, icon('tag'), h('h2', {}, 'No prices to compare'),
        h('p', {}, `Nothing was bought in the last ${snap.window_days} days. Approve a receipt and check again.`), h('a', { class: 'btn btn-primary', href: 'capture.html' }, icon('camera'), 'Scan a receipt'))];
    }
    function header() {
      const s = snap && snap.summary;
      const refundTotal = overcharged.reduce((n, o) => n + o.extra, 0);
      const go = (t) => () => { tab = t; localStorage.setItem('deals:tab', t); render(); };
      const kpi = (label, value, target, cls) => h('button', { class: 'kpi ' + (cls || ''), type: 'button', onclick: go(target) }, h('span', { class: 'value' }, value), h('span', { class: 'label' }, label));
      const alertCount = alertList.length + targets.filter((t) => t.current && t.mode === 'BELOW' && t.current.price <= t.target_price + 1e-9).length;
      return [
        h('div', { class: 'statusline' },
          h('span', {}, snap && snap.generated_at ? `Checked ${RPI.relTime(snap.generated_at)}` : 'Not checked yet'),
          snap && snap.status === 'FAILED' ? h('span', { class: 'pill warn', title: snap.error || '' }, 'last check failed') : null,
          h('button', { class: 'btn btn-sm btn-ghost', type: 'button', disabled: refreshing, onclick: refresh }, icon('refresh'), refreshing ? 'Checking…' : 'Check now')),
        s && s.items_checked ? h('div', { class: 'kpis' },
          kpi('could save a month', s.est_monthly_savings ? money(s.est_monthly_savings) : '—', 'foryou'),
          kpi(`of ${s.items_checked} items cheaper elsewhere`, String(s.cheaper_elsewhere), 'items'),
          refundTotal ? kpi('paid over posted prices', money(refundTotal), 'foryou', 'warnk') : kpi('alerts', String(alertCount), 'alerts')) : null,
        h('div', { class: 'tabs', role: 'tablist' }, ...TABS.map(([k, label]) => {
          const n = k === 'foryou' ? feed().length : k === 'alerts' ? alertCount : 0;
          return h('button', { type: 'button', role: 'tab', 'aria-selected': String(tab === k), class: 'tab' + (tab === k ? ' on' : ''), onclick: go(k) },
            label, n ? h('span', { class: 'count' }, String(n)) : null);
        })),
      ];
    }

    function render() {
      const c = $('content');
      if (!snap) return;
      const body = tab === 'items' ? itemsTab() : tab === 'online' ? onlineTab() : tab === 'alerts' ? alertsTab() : forYouTab();
      c.replaceChildren(...header().filter(Boolean), ...body);
      if (tab === 'items') paintList();
    }

    function paintList() {
      const list = $('list');
      if (!list) return;
      const q = query.trim().toLowerCase();
      const items = snap.best_prices.filter((b) => (!q || b.name.toLowerCase().includes(q)) && (!onlyCheaper || b.saving_pct > 0));
      if (!items.length) return list.replaceChildren(h('p', { class: 'meta', style: 'padding:8px 2px' }, q || onlyCheaper ? 'No items match.' : 'No items.'));
      list.replaceChildren(...items.map(itemCard));
    }

    function itemCard(b) {
      const badge = !b.compared ? h('span', { class: 'pill' }, 'One store so far')
        : b.saving_pct > 0 ? h('span', { class: 'pill ok' }, `Save ${b.saving_pct}% vs ${b.usual.label.split(' · ')[0]}`)
        : h('span', { class: 'pill' }, 'Your usual store is cheapest');
      const setTarget = h('div', { class: 'srow' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: () => targetDialog(b.item_id) }, icon('tag'), 'Tell me when it’s cheaper'));
      return h('details', { class: 'card item' },
        h('summary', {},
          h('div', { class: 'top' }, h('span', { class: 'name' }, b.name), h('span', { class: 'amt' }, (b.cheapest.estimate ? '≈ ' : '') + priceText(b.cheapest.price, b.unit))),
          h('div', { class: 'meta' }, `Cheapest at ${b.cheapest.label} · ${fmtDay(b.cheapest.date)}`),
          h('div', {}, badge)),
        ...b.stores.map((s, i) => h('div', { class: 'srow' },
          h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, s.label),
            h('div', { class: 'meta' }, `Last paid ${fmtDay(s.date)}`, s.estimate ? ` · ≈ ${s.estimate}` : '')),
          i === 0 && b.compared ? h('span', { class: 'pill ok' }, 'Lowest') : null,
          h('div', { class: 'amt' }, (s.estimate ? '≈ ' : '') + priceText(s.price, b.unit)))),
        setTarget);
    }

    // ---------------------------------------------------------------- start
    async function init() {
      try { ctx = await RPI.homeContext(); }
      catch (e) { return $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
      if (!ctx.homeId) return $('content').replaceChildren(h('div', { class: 'empty' }, icon('home'), h('h2', {}, 'No home yet'), h('p', {}, 'Create a home from the receipts page first.')));
      const sw = RPI.homeSwitcher(ctx.homes, ctx.homeId, (id) => { ctx.homeId = id; snap = null; paintHome(); load(); });
      $('homeSwitch').replaceChildren(...(sw ? [sw] : []));
      paintHome(); load();
    }
    function paintHome() {
      const home = ctx.homes.find((x) => x.id === ctx.homeId);
      $('homeLine').textContent = (home ? home.name + ' · ' : '') + 'where each item is cheapest';
    }
    init();
