// analysis.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, money, fmtDate, toast, formDialog, confirmDialog } = RPI;
    const $ = (id) => document.getElementById(id);

    const TABS = [['overview', 'Overview'], ['stores', 'Stores'], ['categories', 'Categories'], ['items', 'Items'], ['prices', 'Prices'], ['savings', 'Savings']];
    const RANGES = [['30d', '30 days'], ['90d', '90 days'], ['12m', '12 months'], ['all', 'All time']];
    let ctx = null, tab = 'overview', range = '90d', token = 0, itemQuery = '', itemSort = 'spend', priceQuery = '';
    let savingsCount = null;

    $('exportBtn').append(icon('download'), 'Export');
    $('exportBtn').addEventListener('click', () => exportDialog());
    const base = () => `api/v1/homes/${encodeURIComponent(ctx.homeId)}/analysis`;

    // ---------------------------------------------------------------- helpers
    const monthLabel = (m) => new Date(+m.slice(0, 4), +m.slice(5, 7) - 1, 1).toLocaleDateString(undefined, { month: 'short', year: m.endsWith('-01') ? '2-digit' : undefined });
    const shortMoney = (n) => (n >= 1000 ? `$${(n / 1000).toFixed(1)}k` : `$${Math.round(n)}`);
    const priceText = (p, unit, cur) => (p == null ? '—' : money(p, cur) + (unit && unit !== 'each' ? '/' + unit : ''));
    const signed = (n) => `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(Math.abs(n) < 10 ? 1 : 0)}%`;
    const empty = (title, text) => h('div', { class: 'empty' }, icon('chart'), h('h2', {}, title), h('p', {}, text));
    const card = (title, ...body) => h('div', { class: 'card' }, title ? h('div', { class: 'card-head' }, h('span', { class: 'card-title' }, title)) : null, ...body);
    const daysText = (d) => (d == null ? '' : d === 0 ? 'today' : d === 1 ? 'yesterday' : `${d} days ago`);
    function trendChip(dir, pctVal) {
      if (pctVal == null) return null;
      // A price that went up is bad news (red), one that went down is good (green).
      return h('span', { class: 'pill ' + (dir === 'up' ? 'danger' : dir === 'down' ? 'ok' : '') }, (dir === 'up' ? '↑ ' : dir === 'down' ? '↓ ' : '') + signed(pctVal));
    }
    function confidence(c) {
      const on = c >= 0.66 ? 3 : c >= 0.33 ? 2 : 1;
      return h('span', { class: 'conf', title: `Confidence ${Math.round(c * 100)}%` }, [1, 2, 3].map((i) => h('i', { class: i <= on ? 'on' : '' })));
    }

    // ---------------------------------------------------------------- shell
    function renderControls() {
      $('tabs').replaceChildren(...TABS.map(([id, label]) => h('button', { type: 'button', role: 'tab', 'aria-pressed': String(tab === id),
        onclick: () => { tab = id; renderControls(); show(); } }, label,
        id === 'savings' && savingsCount ? h('span', { class: 'count' }, savingsCount) : null)));
      const ranges = $('ranges');
      ranges.hidden = tab === 'savings';
      ranges.replaceChildren(...RANGES.map(([id, label]) => h('button', { type: 'button', 'aria-pressed': String(range === id),
        onclick: () => { range = id; renderControls(); show(); } }, label)));
    }

    async function show() {
      const mine = ++token;
      const c = $('content');
      c.replaceChildren(...[0, 1].map(() => h('div', { class: 'skeleton', style: 'height:120px' })));
      try {
        const view = await ({ overview, stores, categories, items, prices, savings }[tab])();
        if (mine !== token) return;
        c.replaceChildren(...view.filter(Boolean));
      } catch (e) {
        if (mine !== token) return;
        c.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message), h('button', { class: 'btn btn-sm', onclick: show }, 'Retry')));
      }
    }

    // ---------------------------------------------------------------- overview
    async function overview() {
      const [ov, rec, disc] = await Promise.all([api(`${base()}/overview?range=${range}`), api(`${base()}/recommendations`).catch(() => null), api(`${base()}/discounts?range=${range}`).catch(() => null)]);
      if (rec) { savingsCount = rec.switch_store.length + rec.price_alerts.length + rec.restock.length; renderControls(); }
      const t = ov.totals;
      if (!t.trips) return [empty('No receipts in this period', 'Approve a receipt, or choose a longer period.')];

      const change = t.change_pct == null ? null : h('span', { class: 'sub' }, `${signed(t.change_pct)} vs previous period`);
      const kpi = (label, value, sub) => h('div', { class: 'card kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), sub ? h('div', { class: 'sub' }, sub) : null);
      const max = Math.max(...ov.by_month.map((m) => m.spend), 1);

      return [
        h('div', { class: 'kpis' },
          kpi('Spent', money(t.spend), change), kpi('Shopping trips', String(t.trips), null),
          kpi('Average basket', money(t.avg_basket), null), kpi('Items · Stores', `${t.distinct_items} · ${t.stores}`, 'distinct items · stores')),
        rec && rec.est_monthly_savings > 0 ? h('div', { class: 'banner ok' }, icon('check'),
          h('div', { class: 'banner-body' }, h('strong', {}, `You could save about ${money(rec.est_monthly_savings)} a month. `),
            h('span', { class: 'banner-text' }, `${rec.switch_store.length} item${rec.switch_store.length === 1 ? ' is' : 's are'} cheaper at another store.`)),
          h('button', { class: 'btn btn-sm', onclick: () => { tab = 'savings'; renderControls(); show(); } }, 'See how')) : null,
        card('Spend by month', h('div', { class: 'card-body' }, h('div', { class: 'bars', role: 'img', 'aria-label': 'Spend by month' },
          ov.by_month.map((m) => h('div', { class: 'bar-col', title: `${monthLabel(m.month)}: ${money(m.spend)} across ${m.trips} trips` },
            h('div', { class: 'bar-val' }, m.spend ? shortMoney(m.spend) : ''),
            h('div', { class: 'bar-stick', style: `height:${m.spend ? Math.max(m.spend / max * 100, 3) : 0}%` }),
            h('div', { class: 'bar-lab' }, monthLabel(m.month))))))),
        card('Where the money goes', h('div', { class: 'card-body flush' }, ov.top_stores.map((s) => h('div', { class: 'rowline' },
          h('div', { class: 'top' }, h('span', { class: 'name' }, s.name), h('span', { class: 'amt' }, money(s.spend))),
          h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${s.share}%` })),
          h('div', { class: 'meta' }, `${s.share}% · ${s.trips} trip${s.trips === 1 ? '' : 's'} · avg ${money(s.avg_basket)}`))))),
        disc && disc.total_saved > 0 ? card('Saved with sales and coupons', h('div', { class: 'card-body flush' },
          h('div', { class: 'rowline' }, h('div', { class: 'top' }, h('span', { class: 'name' }, `${money(disc.total_saved)} saved`), h('span', { class: 'pill ok' }, `${disc.rate_pct}% off those items`)),
            h('div', { class: 'meta' }, `${disc.lines} discounted line${disc.lines === 1 ? '' : 's'} on ${disc.receipts} receipt${disc.receipts === 1 ? '' : 's'}${disc.share_of_spend_pct != null ? ` · ${disc.share_of_spend_pct}% of what you would have spent` : ''}`)),
          ...disc.by_store.slice(0, 3).map((x) => h('div', { class: 'rowline' }, h('div', { class: 'top' }, h('span', { class: 'name' }, x.name), h('span', { class: 'amt' }, money(x.saved))),
            h('div', { class: 'meta' }, `${x.lines} line${x.lines === 1 ? '' : 's'} · ${x.rate_pct}% off`))),
          disc.top_items.length ? h('div', { class: 'rowline' }, h('div', { class: 'meta' }, 'Most saved on: ' + disc.top_items.slice(0, 3).map((i) => `${i.name} (${money(i.saved)})`).join(', '))) : null)) : null,
        card('Top items', h('div', { class: 'card-body flush' }, ov.top_items.length ? ov.top_items.map(itemRow) : h('div', { class: 'rowline meta' }, 'No item prices recorded yet.'))),
      ];
    }

    function itemRow(it, words = []) {
      const bits = [`${it.purchases} purchase${it.purchases === 1 ? '' : 's'}`];
      if (it.avg_interval_days) bits.push(`about every ${Math.round(it.avg_interval_days)} days`);
      bits.push(`last ${daysText(it.days_since_last)}`);
      return h('button', { class: 'rowline', type: 'button', onclick: () => openItem(it.item_id) },
        h('div', { class: 'top' }, h('span', { class: 'name' }, highlighted(it.name, words)), trendChip(it.trend, it.price_change_pct), h('span', { class: 'amt' }, money(it.spend))),
        h('div', { class: 'meta' }, bits.join(' · ') + (it.top_store ? ` · mostly ${it.top_store}` : '')),
        it.overdue_days != null && it.overdue_days > 0 && it.purchases >= 3 && it.days_since_last <= 3 * it.avg_interval_days
          ? h('div', {}, h('span', { class: 'pill warn' }, 'Probably due')) : null);
    }

    // ---------------------------------------------------------------- stores
    async function stores() {
      const r = await api(`${base()}/stores?range=${range}`);
      if (!r.stores.length) return [empty('No receipts in this period', 'Approve a receipt, or choose a longer period.')];
      return [
        h('div', { class: 'small muted' }, `${money(r.totals.spend)} across ${r.totals.trips} trips`),
        ...r.stores.map((s) => h('details', { class: 'card' },
          h('summary', { class: 'rowline', style: 'cursor:pointer;list-style:none' },
            h('div', { class: 'top' }, h('span', { class: 'name' }, s.name), h('span', { class: 'amt' }, money(s.spend))),
            h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${s.share}%` })),
            h('div', { class: 'meta' }, `${s.share}% of spending · ${s.trips} trip${s.trips === 1 ? '' : 's'} · avg basket ${money(s.avg_basket)} · last visit ${fmtDate(s.last_visit)}`)),
          ...(s.locations.length > 1 ? s.locations.map((l) => h('div', { class: 'srow' }, h('div', { class: 'grow small' }, l.label), h('div', { class: 'small muted' }, `${l.trips} trip${l.trips === 1 ? '' : 's'}`), h('div', { class: 'amt' }, money(l.spend)))) : []))),
        h('div', { class: 'small muted' }, h('a', { href: 'stores.html', style: 'text-decoration:underline' }, 'Fix names or merge duplicate stores')),
      ];
    }

    // ---------------------------------------------------------------- CSV export
    function exportDialog() {
      const start = h('input', { class: 'input', type: 'date', 'aria-label': 'From' });
      const end = h('input', { class: 'input', type: 'date', 'aria-label': 'To' });
      const tag = h('input', { class: 'input', type: 'text', placeholder: 'e.g. business (optional)', 'aria-label': 'Tag' });
      const link = (kind, label) => h('a', { class: 'btn btn-primary', download: '', href: '#' }, icon('download'), label);
      const receipts = link('receipts', 'Receipts (one row each)'), lines = link('items', 'Every line');
      const paint = () => {
        const q = new URLSearchParams();
        if (start.value) q.set('start', start.value); if (end.value) q.set('end', end.value); if (tag.value.trim()) q.set('tag', tag.value.trim());
        const suffix = q.toString() ? '?' + q : '';
        receipts.href = RPI.url(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/export/receipts.csv${suffix}`);
        lines.href = RPI.url(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/export/items.csv${suffix}`);
      };
      for (const el of [start, end, tag]) el.addEventListener('input', paint); paint();
      const dlg = h('dialog', { class: 'formdlg' }, h('div', { class: 'dialog-body stack' }, h('h2', {}, 'Export to a spreadsheet'),
        h('p', {}, 'Download saved receipts or every receipt line as a CSV file. Leave the dates empty for everything.'),
        h('label', { class: 'field' }, h('span', { class: 'label' }, 'From'), start), h('label', { class: 'field' }, h('span', { class: 'label' }, 'To'), end),
        h('label', { class: 'field' }, h('span', { class: 'label' }, 'Only receipts tagged'), tag),
        h('div', { class: 'stack' }, receipts, lines)),
        h('div', { class: 'dialog-actions' }, h('button', { class: 'btn', type: 'button', onclick: () => dlg.close() }, 'Close')));
      dlg.addEventListener('close', () => dlg.remove());
      document.body.append(dlg); dlg.showModal();
    }

    // ---------------------------------------------------------------- categories and budgets
    async function categories() {
      const [r, budgets, cats] = await Promise.all([api(`${base()}/categories?range=${range}`), api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/budgets`).catch(() => []),
        api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/categories`).catch(() => [])]);
      const out = [];

      out.push(card('Budgets this month',
        h('div', { class: 'card-body flush' }, budgets.length ? budgets.map((b) => {
          const color = b.status === 'over' ? 'var(--danger)' : b.status === 'warn' ? 'var(--warn)' : 'var(--accent)';
          return h('div', { class: 'rowline' },
            h('div', { class: 'top' }, h('span', { class: 'name' }, b.category || 'All spending'), h('span', { class: 'amt' }, `${money(b.spent)} of ${money(b.amount)}`),
              h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Remove the ${b.category || 'overall'} budget`, onclick: async () => {
                if (!(await confirmDialog({ title: 'Remove this budget?', confirmLabel: 'Remove', danger: true }))) return;
                try { await api('api/v1/budgets/' + b.id, { method: 'DELETE' }); show(); } catch (e) { toast(e.message, 'error'); }
              } }, icon('trash'))),
            h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${Math.min(b.percent, 100)}%;background:${color}` })),
            h('div', { class: 'meta' }, b.spent > b.amount ? `${money(b.spent - b.amount)} over` : `${money(b.remaining)} left`, ` · ${b.days_left} days to go · on course for ${money(b.projected)}`));
        }) : h('div', { class: 'rowline meta' }, 'Set a monthly limit for a category, or for everything, and you will be told when it is nearly used up.')),
        h('div', { class: 'card-body' }, h('button', { class: 'btn btn-sm', type: 'button', onclick: () => budgetDialog(cats) }, icon('plus'), 'Add a budget'))));

      if (!r.categories.length) return [...out, empty('No purchases in this period', 'Approve a receipt, or choose a longer period.')];
      const max = Math.max(...r.categories.map((c) => c.spend), 1);
      out.push(card(`Where the money goes · ${money(r.total)}`, h('div', { class: 'card-body flush' }, r.categories.map((c) => h('div', { class: 'rowline' },
        h('div', { class: 'top' }, h('span', { class: 'name' }, c.category), c.change_pct != null ? h('span', { class: 'pill' + (c.change_pct > 5 ? ' warn' : '') }, signed(c.change_pct)) : null, h('span', { class: 'amt' }, money(c.spend))),
        h('div', { class: 'bar-track' }, h('div', { class: 'bar-fill', style: `width:${c.spend / max * 100}%` })),
        h('div', { class: 'meta' }, `${c.share}% · ${c.top_items.map((i) => i.name).join(', ')}`))))));
      if (r.uncategorized_share > 0) {
        out.push(h('div', { class: 'banner' }, icon('tag'), h('div', { class: 'banner-body' }, h('span', { class: 'banner-text' }, `${r.uncategorized_share}% of spending is on items with no category.`)),
          h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => {
            try { const x = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/common-items/categorize`, { method: 'POST', body: {} }); toast(x.categorized ? `Categorized ${x.categorized} item${x.categorized === 1 ? '' : 's'}${x.left ? `. ${x.left} need a category set by hand (open an item)` : ''}` : 'Nothing could be recognised. Open an item to set its category', x.categorized ? 'success' : 'error'); show(); }
            catch (e) { toast(e.message, 'error'); }
          } }, 'Fill in automatically')));
      }
      return out;
    }

    function budgetDialog(cats) {
      formDialog({ title: 'Add a monthly budget', body: 'Pick a category, or all spending. Setting one that already exists changes its amount.',
        fields: [{ key: 'category', type: 'select', label: 'For', value: '', options: [{ value: '', label: 'All spending' }, ...cats.map((c) => ({ value: c, label: c }))] },
          { key: 'amount', label: 'Amount per month', value: '', placeholder: '400' }],
        submitLabel: 'Save budget',
        onSubmit: (v) => api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/budgets`, { method: 'PUT', body: { category: v.category || null, monthly_amount: v.amount } }) })
        .then((ok) => { if (ok) { toast('Budget saved', 'success'); show(); } });
    }

    // ---------------------------------------------------------------- items (spend + frequency)
    // A search box that filters the list already on the page as you type: no server call, and only the list below it
    // changes, so the box keeps its focus, cursor and the phone's keyboard.
    function searchBox(value, onInput, placeholder = 'Search items') {
      const input = h('input', { class: 'input', type: 'search', placeholder, value, 'aria-label': placeholder, autocomplete: 'off', enterkeyhint: 'search' });
      let frame = null;
      input.addEventListener('input', () => { if (frame) cancelAnimationFrame(frame); frame = requestAnimationFrame(() => onInput(input.value)); });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && input.value) { input.value = ''; onInput(''); e.preventDefault(); }
        if (e.key === 'Enter') input.blur();  // done typing: close the phone keyboard
      });
      return h('div', { class: 'search' }, icon('search'), input);
    }
    // every word typed must be in the name ("milk 2" finds "2% Milk")
    const wordsOf = (q) => q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const matches = (name, words) => { const n = (name || '').toLowerCase(); return words.every((w) => n.includes(w)); };
    function highlighted(name, words) {
      if (!words.length) return name;
      const low = name.toLowerCase(), marks = [];
      for (const w of words) { let i = low.indexOf(w); while (i >= 0) { marks.push([i, i + w.length]); i = low.indexOf(w, i + w.length); } }
      marks.sort((a, b) => a[0] - b[0]);
      const out = []; let at = 0;
      for (const [s, e] of marks) { if (s < at) continue; if (s > at) out.push(name.slice(at, s)); out.push(h('mark', {}, name.slice(s, e))); at = e; }
      if (at < name.length) out.push(name.slice(at));
      return out;
    }

    async function items() {
      // the whole list for the period, once; typing filters it here
      const r = await api(`${base()}/items?range=${range}&sort=${itemSort}`);
      const sort = h('select', { class: 'select', style: 'width:auto', 'aria-label': 'Sort by' },
        [['spend', 'Most spent'], ['frequency', 'Bought most often'], ['recent', 'Bought recently'], ['name', 'Name']].map(([v, l]) => h('option', { value: v }, l)));
      sort.value = itemSort;
      sort.addEventListener('change', () => { itemSort = sort.value; show(); });
      const title = h('span', { class: 'card-title' });
      const listHost = h('div', { class: 'card-body flush' });
      const paint = () => {
        const words = wordsOf(itemQuery);
        const shown = words.length ? r.items.filter((it) => matches(it.name, words)) : r.items;
        const spend = shown.reduce((n, it) => n + (it.spend || 0), 0);
        title.textContent = words.length ? `${shown.length} of ${r.items.length} items · ${money(spend)}` : `${r.items.length} items · ${money(r.total_spend)}`;
        listHost.replaceChildren(...(shown.length ? shown.map((it) => itemRow(it, words))
          : [h('div', { class: 'rowline meta' }, words.length ? `No items match “${itemQuery.trim()}”.` : 'No item prices recorded in this period.')]));
      };
      const box = searchBox(itemQuery, (v) => { itemQuery = v; paint(); });
      paint();
      return [h('div', { class: 'row wrap' }, box, sort),
        h('div', { class: 'card' }, h('div', { class: 'card-head' }, title), listHost),
        h('p', { class: 'small muted' }, 'Items are grouped by common name. Tap one to rename it or merge it with another.')];
    }

    // ---------------------------------------------------------------- prices across stores
    async function prices() {
      const [r, ix] = await Promise.all([api(`${base()}/prices?range=${range}`), api(`${base()}/inflation?months=12`).catch(() => null)]);
      const indexCard = ix && ix.available ? h('div', { class: 'card' }, h('div', { class: 'card-body stack' },
        h('div', { class: 'row-between' }, h('div', {}, h('div', { class: 'kpi-label small muted' }, 'Your price index'),
          h('div', { style: 'font-size:1.25rem;font-weight:700' }, `${ix.change_pct > 0 ? '+' : ix.change_pct < 0 ? '−' : ''}${Math.abs(ix.change_pct).toFixed(1)}%`,
            h('span', { class: 'small muted', style: 'font-weight:400' }, ` over ${ix.months} months`))),
          RPI.sparkline(ix.series.map((p) => p.index), { w: 120, h: 40, direction: ix.change_pct >= 3 ? 'up' : ix.change_pct <= -3 ? 'down' : 'flat' })),
        h('div', { class: 'small muted' }, `The things you keep buying (${ix.items_used} items) cost this much more or less than at the start. It follows your own basket, not the national average.`),
        ix.risers.length ? h('div', { class: 'small' }, 'Up most: ' + ix.risers.slice(0, 3).map((m) => `${m.name} ${signed(m.change_pct)}`).join(', ')) : null,
        ix.fallers.length ? h('div', { class: 'small' }, 'Down most: ' + ix.fallers.slice(0, 3).map((m) => `${m.name} ${signed(m.change_pct)}`).join(', ')) : null))
        : (ix && ix.reason ? h('p', { class: 'small muted' }, ix.reason) : null);
      if (!r.items.length) return [indexCard, empty('No prices yet', 'Prices are recorded when you approve a receipt.')];
      const listHost = h('div', { class: 'stack' });
      const count = h('div', { class: 'small muted' });
      const paint = () => {
        const words = wordsOf(priceQuery);
        const shown = words.length ? r.items.filter((it) => matches(it.name, words)) : r.items;
        count.textContent = words.length ? `${shown.length} of ${r.items.length} items` : '';
        listHost.replaceChildren(...(shown.length ? shown.map((it) => priceCard(it, words))
          : [empty('No matching items', `Nothing is called “${priceQuery.trim()}”. Try a different word.`)]));
      };
      const box = searchBox(priceQuery, (v) => { priceQuery = v; paint(); });
      paint();
      return [indexCard, box, count, listHost];
    }

    function priceCard(it, words) {
      return h('div', { class: 'card price-card' },
        h('div', { class: 'head' },
          h('div', { class: 'grow' }, h('button', { type: 'button', onclick: () => openItem(it.item_id) }, highlighted(it.name, words)),
            h('div', { class: 'small muted' }, `${it.observations} price${it.observations === 1 ? '' : 's'} recorded${it.unit && it.unit !== 'each' ? ` · per ${it.unit}` : ''}`)),
          trendChip(it.trend.direction, it.trend.change_pct),
          RPI.sparkline(it.trend.series.map((p) => p.price), { direction: it.trend.direction })),
        ...it.stores.map((s) => h('div', { class: 'srow' },
          h('div', { class: 'grow' }, h('div', { style: 'font-weight:600' }, s.label), h('div', { class: 'small muted' }, `${fmtDate(s.latest_date)}${s.observations > 1 ? ` · avg ${priceText(s.avg_price, s.unit)}` : ''}${s.estimate ? ` · ≈ ${s.estimate}` : ''}`)),
          s.cheapest ? h('span', { class: 'pill ok' }, 'Lowest') : (s.premium_pct ? h('span', { class: 'small muted' }, `+${s.premium_pct}%`) : null),
          h('div', { class: 'amt' }, (s.estimate ? '≈ ' : '') + priceText(s.latest_price, s.unit)))));
    }

    // ---------------------------------------------------------------- savings
    async function savings() {
      const r = await api(`${base()}/recommendations`);
      savingsCount = r.switch_store.length + r.price_alerts.length + r.restock.length; renderControls();
      const out = [];
      const nothing = !savingsCount && !r.store_scorecard.length;

      out.push(h('div', { class: 'small muted' }, `Based on your last ${r.window_days} days of receipts. Savings are estimates from what you actually paid.`));
      if (r.est_monthly_savings > 0) {
        out.push(h('div', { class: 'banner ok' }, icon('check'), h('div', { class: 'banner-body' },
          h('strong', {}, `About ${money(r.est_monthly_savings)} a month`), h('span', { class: 'banner-text' }, ' if you bought these at the cheaper store.'))));
      }
      if (nothing) {
        out.push(empty('Nothing to suggest yet', 'Suggestions appear once the same item has been bought at two different stores, or a few times at one. Keep scanning receipts.'));
        return out;
      }

      if (r.switch_store.length) {
        out.push(h('div', { class: 'sect' }, 'Cheaper elsewhere'));
        for (const s of r.switch_store) {
          out.push(h('div', { class: 'card rec' },
            h('div', { class: 'headline' }, h('button', { type: 'button', style: 'background:none;border:0;padding:0;font:inherit;color:inherit;cursor:pointer;text-align:left', onclick: () => openItem(s.item_id) }, s.item_name)),
            h('div', {}, 'Buy at ', h('strong', {}, s.to.label), ` for ${priceText(s.to.price, s.unit)}`, ` — ${s.saving_pct}% less than `, h('strong', {}, s.from.label), ` (${priceText(s.from.price, s.unit)})`),
            h('div', { class: 'small muted row wrap' },
              s.est_monthly_savings ? h('span', {}, 'Saves about ', h('span', { class: 'money' }, money(s.est_monthly_savings)), '/month') : null,
              confidence(s.confidence), h('span', {}, s.confidence >= 0.66 ? 'Solid evidence' : s.confidence >= 0.33 ? 'Some evidence' : 'Early hint')),
            h('div', { class: 'small muted' }, `You bought it ${s.from.receipts}× at ${s.from.label.split(' · ')[0]}. The lower price was seen ${fmtDate(s.to.date)}.`)));
        }
      }
      if (r.price_alerts.length) {
        out.push(h('div', { class: 'sect' }, 'Prices worth noticing'));
        for (const a of r.price_alerts) {
          out.push(h('div', { class: 'card rec' },
            h('div', { class: 'row' }, h('span', { class: 'headline' }, a.item_name), h('span', { class: 'pill ' + (a.type === 'PRICE_UP' ? 'danger' : 'ok') }, a.type === 'PRICE_UP' ? '↑ ' + signed(a.change_pct) : '↓ ' + signed(a.change_pct))),
            h('div', {}, `${priceText(a.price, a.unit)} at ${a.store}, usually ${priceText(a.usual_price, a.unit)}`),
            h('div', { class: 'small muted' }, a.type === 'PRICE_UP' ? 'Might be worth waiting or trying another store.' : 'A good price. Worth stocking up if it keeps.', ` Seen ${fmtDate(a.date)}.`)));
        }
      }
      if (r.restock.length) {
        out.push(h('div', { class: 'sect' }, 'Probably time to buy'));
        out.push(card(null, h('div', { class: 'card-body flush' }, r.restock.map((s) => h('button', { class: 'rowline', type: 'button', onclick: () => openItem(s.item_id) },
          h('div', { class: 'top' }, h('span', { class: 'name' }, s.item_name), h('span', { class: 'pill ' + (s.status === 'overdue' ? 'warn' : '') }, s.status === 'overdue' ? 'Overdue' : 'Due')),
          h('div', { class: 'meta' }, `You usually buy it every ${Math.round(s.avg_interval_days)} days. It's been ${s.days_since_last}.`))))));
      }
      if (r.store_scorecard.length) {
        out.push(h('div', { class: 'sect' }, 'Store scorecard'));
        out.push(card(null, h('table', { class: 'tbl' },
          h('thead', {}, h('tr', {}, h('th', {}, 'Store'), h('th', { class: 'r' }, 'Cheapest on'), h('th', { class: 'r' }, 'Avg. premium'))),
          h('tbody', {}, r.store_scorecard.map((s) => h('tr', {}, h('td', {}, s.name), h('td', { class: 'r' }, `${s.cheapest_count} of ${s.items_compared}`), h('td', { class: 'r' }, s.avg_premium_pct ? `+${s.avg_premium_pct}%` : '—')))))));
        out.push(h('p', { class: 'small muted' }, 'Only items you have bought at two or more stores are compared, in the same unit.'));
      }
      return out;
    }

    // ---------------------------------------------------------------- item detail
    async function openItem(itemId) {
      let d;
      try { d = await api(`${base()}/items/${encodeURIComponent(itemId)}?range=all`); } catch (e) { return toast(e.message, 'error'); }
      const s = d.stats;
      const dlg = h('dialog', { class: 'wide' });
      const body = h('div', { class: 'dialog-body' });
      dlg.append(body, h('div', { class: 'dialog-actions' }, h('button', { class: 'btn', onclick: () => dlg.close() }, 'Close')));
      let changed = false;
      dlg.addEventListener('close', () => { dlg.remove(); if (changed) { savingsCount = null; show(); } });

      const name = h('input', { class: 'input', value: d.item.name, maxlength: '255', 'aria-label': 'Common name' });
      const save = h('button', { class: 'btn btn-primary', type: 'button', onclick: async () => {
        try { await api('api/v1/common-items/' + itemId, { method: 'PATCH', body: { name: name.value } }); toast('Name saved', 'success'); changed = true; dlg.close(); }
        catch (e) { toast(e.message, 'error'); }
      } }, 'Save name');
      const catSel = h('select', { class: 'select', 'aria-label': 'Category' }, h('option', { value: '' }, 'No category'));
      api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/categories`).then((list) => {
        for (const c of list) catSel.append(h('option', { value: c }, c));
        if (d.item.category && !list.includes(d.item.category)) catSel.append(h('option', { value: d.item.category }, d.item.category));
        catSel.value = d.item.category || '';
      }).catch(() => {});
      catSel.addEventListener('change', async () => {
        try { await api('api/v1/common-items/' + itemId, { method: 'PATCH', body: { category: catSel.value } }); toast('Category saved', 'success'); changed = true; }
        catch (e) { toast(e.message, 'error'); }
      });
      const merge = h('button', { class: 'btn', type: 'button', onclick: async () => {
        const list = (await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/common-items`)).filter((x) => x.id !== itemId);
        if (!list.length) return toast('There is no other item to merge into', 'error');
        const ok = await formDialog({ title: `Merge “${d.item.name}”`, body: 'All its purchases, prices and printed names move to the item you pick. This can’t be undone.',
          fields: [{ key: 'into', label: 'Merge into', type: 'select', options: list.map((x) => ({ value: x.id, label: x.name })), value: list[0].id }],
          submitLabel: 'Merge', danger: true, onSubmit: (v) => api(`api/v1/common-items/${itemId}/merge`, { method: 'POST', body: { into_item_id: v.into } }) });
        if (ok) { toast('Items merged', 'success'); changed = true; dlg.close(); }
      } }, 'Merge into…');

      const kv = (v, l) => h('div', {}, h('b', {}, v), h('span', {}, l));
      const series = Object.entries(d.series).map(([loc, pts]) => ({ label: (d.stores.find((x) => x.location_id === loc) || {}).label || 'Store', points: pts }));
      body.append(
        h('h2', {}, d.item.name),
        h('p', { class: 'small' }, d.item.aliases.length ? 'Printed on receipts as: ' + d.item.aliases.join(' · ') : ''),
        d.item.name_confirmed ? null : h('p', { class: 'small', style: 'color:var(--warn)' }, 'This item still uses its printed name. Give it a common name so it groups cleanly.'),
        h('div', { class: 'renamerow' }, name, save), h('label', { class: 'field', style: 'margin:6px 0' }, h('span', { class: 'label' }, 'Category'), catSel), h('div', { class: 'row', style: 'margin-bottom:6px' }, merge),
        s ? h('div', { class: 'kv4' }, kv(money(s.spend), 'total spent'), kv(String(s.purchases), 'purchases'),
          kv(s.avg_interval_days ? `~${Math.round(s.avg_interval_days)} days` : '—', 'between purchases'), kv(s.next_expected_date ? fmtDate(s.next_expected_date) : '—', 'next expected')) : null,
        series.length ? h('div', {}, h('div', { class: 'sect' }, 'Price over time'), RPI.lineChart(series, { format: (n) => (n >= 10 ? n.toFixed(0) : n.toFixed(2)) }),
          h('div', { class: 'legend' }, series.map((x, i) => h('span', {}, h('i', { style: `background:${RPI.CHART_COLORS[i % RPI.CHART_COLORS.length]}` }), x.label)))) : null,
        d.stores.length ? h('div', {}, h('div', { class: 'sect', style: 'margin-top:14px' }, 'By store'),
          h('table', { class: 'tbl', style: 'margin-top:4px' }, h('thead', {}, h('tr', {}, h('th', {}, 'Store'), h('th', { class: 'r' }, 'Latest'), h('th', { class: 'r' }, 'Average'), h('th', { class: 'r' }, 'Lowest'))),
            h('tbody', {}, d.stores.map((x) => h('tr', {}, h('td', {}, x.label, x.cheapest ? h('span', { class: 'pill ok', style: 'margin-left:8px' }, 'Lowest') : null),
              h('td', { class: 'r' }, priceText(x.latest_price, x.unit)), h('td', { class: 'r' }, priceText(x.avg_price, x.unit)), h('td', { class: 'r' }, priceText(x.min_price, x.unit))))))) : null,
        d.history.length ? h('div', {},
          h('div', { class: 'sect', style: 'margin-top:14px' }, 'Recent purchases'),
          h('table', { class: 'tbl', style: 'margin-top:4px' },
            h('tbody', {}, d.history.slice(0, 12).map((x) => h('tr', {},
              h('td', {}, fmtDate(x.date)),
              h('td', {}, x.store),
              h('td', { class: 'r' }, priceText(x.price, x.unit),
                x.comparable || x.price == null ? null : h('span', { class: 'pill', style: 'margin-left:6px', title: 'Priced in a different unit, so not in the comparison above' }, 'other unit')),
              h('td', { class: 'r' }, x.total == null ? '—' : money(x.total)))))),
          d.other_unit_purchases ? h('p', { class: 'small muted', style: 'margin-top:6px' },
            `${d.other_unit_purchases} purchase${d.other_unit_purchases === 1 ? ' was' : 's were'} priced in a different unit (for example each instead of per lb), so ${d.other_unit_purchases === 1 ? 'it is' : 'they are'} not in the store comparison or price chart. The amount paid is still counted in your spending.`) : null) : null);
      document.body.append(dlg); dlg.showModal();
    }

    // ---------------------------------------------------------------- start
    async function init() {
      try { ctx = await RPI.homeContext(); }
      catch (e) { return $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
      if (!ctx.homeId) return $('content').replaceChildren(empty('No home yet', 'Create a home from the receipts page first.'));
      const sw = RPI.homeSwitcher(ctx.homes, ctx.homeId, (id) => { ctx.homeId = id; savingsCount = null; paintHome(); renderControls(); show(); });
      $('homeSwitch').replaceChildren(...(sw ? [sw] : []));
      paintHome(); renderControls(); show();
    }
    function paintHome() {
      const home = ctx.homes.find((x) => x.id === ctx.homeId);
      $('homeLine').textContent = (home ? home.name + ' · ' : '') + 'spending, prices and savings';
    }
    init();
