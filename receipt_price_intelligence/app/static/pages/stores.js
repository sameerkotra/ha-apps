// stores.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, money, fmtDate, toast, confirmDialog, formDialog } = RPI;
    const $ = (id) => document.getElementById(id);
    let ctx = null, dir = null, web = { configured: false }, suggestions = {}, lookupText = null;
    let near = null, nearBusy = false, nearRadius = '', nearShowHidden = false, nearOpen = false;
    let siteSuggestions = [], sitesBusy = null, storeDiscounts = {};

    const isAdmin = () => ctx && ctx.me.is_admin;

    // ---------------------------------------------------------------- data
    async function load() {
      const c = $('content');
      c.replaceChildren(...[0, 1].map(() => h('div', { class: 'skeleton', style: 'height:140px' })));
      try {
        dir = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/store-directory`);
        web = await api('api/v1/webinfo/status').catch(() => ({ configured: false }));
        suggestions = {};
        // hours suggestions also come from the nearby-store search (OpenStreetMap), with no search server
        for (const x of await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/webinfo/hours`).catch(() => [])) suggestions[x.location_id] = x;
        near = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/nearby-stores`).catch(() => null);
        storeDiscounts = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/store-discounts`).catch(() => ({}));
      } catch (e) {
        return c.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message),
          h('button', { class: 'btn btn-sm', onclick: load }, 'Retry')));
      }
      render();
    }
    const reload = () => load();

    // ---------------------------------------------------------------- render
    const chainById = (id) => dir.chains.find((c) => c.id === id);
    const locById = (id) => { for (const c of dir.chains) for (const l of c.locations) if (l.id === id) return { chain: c, loc: l }; return null; };
    const addressText = (a) => a.raw || [a.street, [a.city, [a.state, a.postal_code].filter(Boolean).join(' ')].filter(Boolean).join(', ')].filter(Boolean).join(', ');

    function render() {
      renderDupes();
      $('nearbyBox').replaceChildren(...nearbyCard());
      $('sitesBox').replaceChildren(...sitesCard());
      $('webBox').replaceChildren(...webCard());
      const c = $('content');
      if (!dir.chains.length) {
        return c.replaceChildren(h('div', { class: 'empty' }, icon('store'), h('h2', {}, 'No stores yet'),
          h('p', {}, 'Stores appear here when you approve a receipt.'), h('a', { class: 'btn btn-primary', href: 'capture.html' }, icon('camera'), 'Scan a receipt')));
      }
      c.replaceChildren(...[...dir.chains.map(chainCard),
        isAdmin() ? null : h('p', { class: 'note' }, 'Anyone can fix names and addresses. Merging and deleting stores is for administrators.')].filter(Boolean));
    }

    function renderDupes() {
      const box = $('dupes');
      const items = [];
      for (const p of dir.similar_chains) {
        const a = chainById(p.a), b = chainById(p.b);
        if (!a || !b) continue;
        items.push(dupeBanner(`“${a.name}” and “${b.name}” may be the same store.`, p.reason,
          () => mergeChain(a, b.id)));
      }
      for (const p of dir.duplicate_locations) {
        const a = locById(p.a), b = locById(p.b);
        if (!a || !b) continue;
        items.push(dupeBanner(`Two ${a.chain.name} locations share an address: ${a.loc.label.split(' · ').slice(1).join(' · ') || addressText(a.loc.address)}.`, p.reason,
          () => mergeLocation(a.loc, b.loc.id)));
      }
      box.replaceChildren(...items);
    }
    function dupeBanner(text, reason, onMerge) {
      return h('div', { class: 'banner warn' }, icon('alert'),
        h('div', { class: 'banner-body' }, h('strong', {}, 'Possible duplicate. '), h('span', { class: 'banner-text' }, text),
          h('div', { class: 'small banner-text muted' }, isAdmin() ? reason : `${reason}. An administrator can merge them.`)),
        isAdmin() ? h('button', { class: 'btn btn-sm', onclick: onMerge }, 'Merge…') : null);
    }

    function menu(...items) {
      const list = items.filter(Boolean);
      const d = h('details', { class: 'menu' },
        h('summary', { class: 'icon-btn', 'aria-label': 'More actions' }, icon('more')),
        h('div', { class: 'menu-list' }, list.map(([label, fn, cls]) => h('button', { type: 'button', class: cls || '', onclick: () => { d.open = false; fn(); } }, label))));
      return d;
    }
    document.addEventListener('click', (e) => { document.querySelectorAll('details.menu[open]').forEach((m) => { if (!m.contains(e.target)) m.open = false; }); });

    function chainCard(chain) {
      const meta = [`${chain.locations.length} location${chain.locations.length === 1 ? '' : 's'}`, `${chain.receipt_count} receipt${chain.receipt_count === 1 ? '' : 's'}`,
        chain.receipt_count ? money(chain.spend) : null].filter(Boolean).join(' · ');
      return h('div', { class: 'card chain' },
        h('div', { class: 'chain-head' },
          h('div', { class: 'avatar' }, (chain.name[0] || '?').toUpperCase()),
          h('div', { class: 'main' }, h('div', { class: 'name' }, chain.name), h('div', { class: 'meta' }, meta)),
          h('button', { class: 'icon-btn', type: 'button', 'aria-label': `Rename ${chain.name}`, onclick: () => renameChain(chain) }, icon('edit')),
          menu(
            ['Add a location', () => locationForm(chain, null)],
            ['Website and pages…', () => webPagesDialog(chain)],
            ['Discounts…', () => discountsDialog(chain)],
            isAdmin() && dir.chains.length > 1 ? ['Merge into another store…', () => mergeChain(chain)] : null,
            isAdmin() && chain.deletable ? ['Delete store', () => deleteChain(chain), 'danger'] : null)),
        chain.aliases.length ? h('div', { class: 'aliases' }, 'Also printed as: ' + chain.aliases.join(', ')) : null,
        (storeDiscounts[chain.id] || []).length ? h('div', { class: 'aliases' }, 'Discounts: ' + storeDiscounts[chain.id].map(discountText).join(' · ')) : null,
        chain.website || chain.search_url || chain.deals_url ? h('div', { class: 'aliases' }, 'Web: ',
          ...[[chain.website, chain.website ? hostOf(chain.website) : ''], [chain.search_url, 'product search'], [chain.deals_url, 'weekly ad']]
            .filter(([u]) => u).flatMap(([u, label], i) => [i ? ' · ' : null, h('a', { href: u.replace('{query}', 'milk'), target: '_blank', rel: 'noopener', style: 'text-decoration:underline' }, label)])) : null,
        ...chain.locations.map((loc) => locationRow(chain, loc)),
        chain.locations.length ? null : h('div', { class: 'chain-foot small muted' }, 'No locations yet.'));
    }

    function locationRow(chain, loc) {
      const title = [loc.store_number ? `Store #${loc.store_number}` : 'Location', loc.name && loc.name !== chain.name ? loc.name : null].filter(Boolean).join(' · ');
      const addr = addressText(loc.address);
      return h('div', { class: 'loc' },
        h('div', { class: 'main' }, h('div', { class: 'title' }, title), h('div', { class: 'addr' + (addr ? '' : ' none') }, addr || 'No address yet'),
          loc.opening_hours ? h('div', { class: 'addr' }, hoursSummary(loc.opening_hours)) : null,
          loc.page_url ? h('div', { class: 'addr' }, h('a', { href: loc.page_url, target: '_blank', rel: 'noopener', style: 'text-decoration:underline' }, 'Store page: ' + hostOf(loc.page_url))) : null,
          suggestionBox(loc)),
        h('div', { class: 'stat' }, h('b', {}, loc.receipt_count ? money(loc.spend) : '—'), `${loc.receipt_count} receipt${loc.receipt_count === 1 ? '' : 's'}`),
        h('button', { class: 'icon-btn', type: 'button', 'aria-label': 'Edit location', onclick: () => locationForm(chain, loc) }, icon('edit')),
        menu(
          ['Opening hours…', () => hoursDialog(chain, loc)],
          ['Store web page…', () => storePageDialog(chain, loc)],
          web.configured ? ['Find hours online', () => lookupHours([loc.id])] : null,
          isAdmin() && countLocations() > 1 ? ['Merge into another location…', () => mergeLocation(loc)] : null,
          isAdmin() && loc.deletable ? ['Delete location', () => deleteLocation(loc), 'danger'] : null));
    }
    const countLocations = () => dir.chains.reduce((n, c) => n + c.locations.length, 0);

    // ---------------------------------------------------------------- filling in websites
    const paintSites = () => $('sitesBox').replaceChildren(...sitesCard());
    function sitesCard() {
      if (!dir) return [];
      const known = new Set(dir.chains.filter((c) => c.website).map((c) => c.id));
      const pending = siteSuggestions.filter((s) => !known.has(s.chain_id));
      const missing = dir.chains.filter((c) => !c.website && c.locations.length).length;
      if (!missing && !pending.length && !sitesBusy) return [];
      const row = (s) => h('div', { class: 'row wrap', style: 'gap:8px;align-items:center;justify-content:space-between;border-top:1px solid var(--line);padding-top:8px' },
        h('div', { style: 'flex:1;min-width:12rem' }, h('div', { style: 'font-weight:600' }, s.name),
          h('div', { class: 'small muted' }, s.domain, s.matches_name ? '' : ' · does not carry the store\'s name, check it',
            s.source_url ? h('a', { href: s.source_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, 'open') : null)),
        h('div', { class: 'row', style: 'gap:6px' },
          h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: () => acceptSites([s]) }, 'Use'),
          h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => { siteSuggestions = siteSuggestions.filter((x) => x !== s); paintSites(); } }, 'Skip')));
      return [h('div', { class: 'card', style: 'margin-bottom:14px' }, h('div', { class: 'card-body stack' },
        h('div', { class: 'row wrap', style: 'gap:8px;align-items:center;justify-content:space-between' },
          h('div', {}, h('strong', {}, 'Store websites'), h('div', { class: 'note' }, sitesBusy
            || (pending.length ? `${pending.length} suggested below: check each and tap Use.`
              : `${missing} store${missing === 1 ? ' has' : 's have'} no website yet. With one, lookups check the store's own site first.`))),
          missing ? h('button', { class: 'btn btn-sm', type: 'button', disabled: !!sitesBusy, onclick: fillSites }, icon('search'), sitesBusy ? 'Working…' : 'Fill in websites') : null),
        ...pending.map(row),
        pending.length > 1 ? h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => acceptSites(pending.filter((s) => s.matches_name)) },
          `Use all ${pending.filter((s) => s.matches_name).length} that carry the store's name`) : null))];
    }
    async function fillSites() {
      sitesBusy = 'Taking websites from your stores\' own pages…'; paintSites();
      try {
        const r = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/store-websites/fill`, { method: 'POST', body: { search: true } });
        if (r.applied.length) toast(`Website set for ${r.applied.map((a) => a.name).join(', ')} (from their store pages)`, 'success');
        if (r.job) {
          const done = await RPI.followJob(r.job.id, (j) => { sitesBusy = `${j.message} (${j.done} of ${j.total})`; paintSites(); });
          siteSuggestions = done.suggestions || [];
          if (done.error) toast(done.error, 'error');
          else if (!siteSuggestions.length) toast('No clear website was found for the other stores. You can add them from each store\'s menu.', 'info');
        } else if (r.missing && !r.search_available) {
          toast('Set up web search in Admin → App settings to look up the other stores\' websites, or add them from each store\'s menu.', 'info');
        } else if (!r.applied.length && r.missing) {
          toast('No websites could be found from your stores\' pages. Add them from each store\'s menu.', 'info');
        }
      } catch (e) { toast(e.message, 'error'); }
      sitesBusy = null;
      await load();
    }
    async function acceptSites(list) {
      let saved = 0;
      for (const s of list) {
        try { await api(`api/v1/store-chains/${s.chain_id}/web`, { method: 'PUT', body: { website: s.website } }); saved++; }
        catch (e) { toast(`${s.name}: ${e.message}`, 'error'); }
      }
      if (saved) toast(`Website saved for ${saved} store${saved === 1 ? '' : 's'}`, 'success');
      siteSuggestions = siteSuggestions.filter((s) => !list.includes(s));
      await load();
    }

    // ---------------------------------------------------------------- store discounts (cards, memberships)
    const discountText = (d) => [d.name, d.percent ? `${d.percent}%` : null, d.cents_per_unit ? `${d.cents_per_unit}¢/gal` : null, d.applies_to_text].filter(Boolean).join(' ');
    async function discountsDialog(chain) {
      const now = storeDiscounts[chain.id] || [];
      const scope = [{ value: 'ALL', label: 'Everything (fuel too)' }, { value: 'NON_FUEL', label: 'Everything except fuel' }, { value: 'FUEL', label: 'Fuel only' }];
      const slot = (n, d) => [
        { key: `name${n}`, label: `Discount ${n}: name`, value: d ? d.name : '', placeholder: n === 1 ? `${chain.name} credit card` : 'Fuel discount' },
        { key: `percent${n}`, label: 'Percent off', value: d && d.percent ? String(d.percent) : '', placeholder: n === 1 ? '5' : '' },
        { key: `scope${n}`, label: 'Applies to', type: 'select', options: scope, value: d ? d.applies_to : (n === 1 ? 'ALL' : 'FUEL') },
        { key: `cents${n}`, label: 'Fuel: cents off per gallon (optional)', value: d && d.cents_per_unit ? String(d.cents_per_unit) : '', placeholder: '0',
          hint: n === 2 ? 'Leave a discount empty to remove it.' : null },
      ];
      const ok = await formDialog({
        title: `Discounts at ${chain.name}`,
        body: 'What you save at this store on top of its prices: a store credit card, a membership reward. It is taken off prices wherever the app works out where things are cheapest (trip planner, shopping list), on online and receipt prices alike, since cashback is not on the receipt.',
        fields: [...slot(1, now[0]), ...slot(2, now[1])],
        onSubmit: (v) => {
          const list = [1, 2].map((n) => ({ name: v[`name${n}`].trim() || null, percent: Number(v[`percent${n}`] || 0), applies_to: v[`scope${n}`], cents_per_unit: Number(v[`cents${n}`] || 0) }))
            .filter((d) => d.percent || d.cents_per_unit);
          for (const d of list) if (Number.isNaN(d.percent) || Number.isNaN(d.cents_per_unit)) throw new Error('Discounts must be numbers');
          return api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/store-chains/${chain.id}/discounts`, { method: 'PUT', body: { discounts: list } });
        },
      });
      if (ok) { toast('Discounts saved', 'success'); reload(); }
    }

    // ---------------------------------------------------------------- store web pages
    const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (_) { return u; } };
    async function webPagesDialog(chain) {
      const ok = await formDialog({
        title: `${chain.name} on the web`,
        body: 'Online price, deal and hours lookups open these pages directly before searching, and searches look on the store\'s own site first. Leave a field empty if you don\'t know it.',
        fields: [
          { key: 'website', label: 'Website', value: chain.website || '', placeholder: 'kroger.com', hint: 'Searches look on this site first.' },
          { key: 'search_url', label: 'Product search page', value: chain.search_url || '', placeholder: 'https://www.kroger.com/search?query={query}',
            hint: 'Search the store\'s site for any product, copy the address of the results page, and put {query} where the product name was.' },
          { key: 'deals_url', label: 'Weekly ad or deals page', value: chain.deals_url || '', placeholder: 'https://www.kroger.com/weeklyad' },
        ],
        onSubmit: (v) => api(`api/v1/store-chains/${chain.id}/web`, { method: 'PUT', body: v }),
      });
      if (ok) { toast('Web pages saved', 'success'); reload(); }
    }
    async function storePageDialog(chain, loc) {
      const ok = await formDialog({
        title: 'Store web page',
        body: `This store's own page on ${chain.website ? hostOf(chain.website) : 'the chain\'s website'} (the one with its address and hours). Hours lookups read it first. Leave empty to remove it.`,
        fields: [{ key: 'page_url', label: 'Page address', value: loc.page_url || '', placeholder: 'https://www.example.com/stores/123' }],
        onSubmit: (v) => api('api/v1/store-locations/' + loc.id, { method: 'PATCH', body: { page_url: v.page_url } }),
      });
      if (ok) { toast('Store page saved', 'success'); reload(); }
    }

    // ---------------------------------------------------------------- actions
    async function renameChain(chain) {
      const ok = await formDialog({
        title: 'Rename store', body: 'The old name keeps working: receipts that print it still go to this store. This changes it for everyone who shops here.',
        fields: [{ key: 'name', label: 'Store name', value: chain.name }],
        onSubmit: (v) => api('api/v1/store-chains/' + chain.id, { method: 'PATCH', body: { name: v.name } }),
        conflictAction: isAdmin() ? { label: 'Merge into that store instead', run: (id) => api(`api/v1/store-chains/${chain.id}/merge`, { method: 'POST', body: { into_chain_id: id } }) } : null,
      });
      if (ok) { toast('Store updated', 'success'); reload(); }
    }

    async function mergeChain(chain, preselect) {
      const options = dir.chains.filter((c) => c.id !== chain.id).map((c) => ({ value: c.id, label: c.name }));
      const ok = await formDialog({
        title: `Merge “${chain.name}”`,
        body: 'Every receipt and price moves to the store you pick, and this name will point to it from now on. This can’t be undone.',
        fields: [
          { key: 'into', label: 'Merge into', type: 'select', options, value: preselect || options[0].value },
          { key: 'combine', type: 'checkbox', value: true, label: 'Combine into a single store',
            hint: 'Every receipt from both stores is kept under one location. Untick to keep each location separate.' },
        ],
        submitLabel: 'Merge', danger: true,
        onSubmit: (v) => api(`api/v1/store-chains/${chain.id}/merge`, { method: 'POST', body: { into_chain_id: v.into, combine_locations: v.combine } }),
      });
      if (ok) { toast('Stores merged', 'success'); reload(); }
    }

    async function deleteChain(chain) {
      if (!(await confirmDialog({ title: `Delete “${chain.name}”?`, body: 'It has no receipts.', confirmLabel: 'Delete', danger: true }))) return;
      try { await api('api/v1/store-chains/' + chain.id, { method: 'DELETE' }); toast('Store deleted'); reload(); } catch (e) { toast(e.message, 'error'); }
    }

    async function locationForm(chain, loc) {
      const a = loc ? loc.address : {};
      const fields = [
        { key: 'store_number', label: 'Store number', value: loc ? loc.store_number : '', maxlength: 50, placeholder: 'e.g. 412' },
        { key: 'name', label: 'Label (optional)', value: loc && loc.name !== chain.name ? loc.name : '', placeholder: 'e.g. The one on Main St' },
        { key: 'street', label: 'Street', value: a.street || '' },
        { key: 'city', label: 'City', value: a.city || '' },
        { key: 'state', label: 'State', value: a.state || '', maxlength: 100 },
        { key: 'postal_code', label: 'ZIP / postal code', value: a.postal_code || '', maxlength: 20 },
      ];
      if (loc && dir.chains.length > 1) {
        fields.push({ key: 'store_chain_id', label: 'Store (chain)', type: 'select', value: chain.id, options: dir.chains.map((c) => ({ value: c.id, label: c.name })) });
      }
      const ok = await formDialog({
        title: loc ? `Edit ${chain.name} location` : `Add a ${chain.name} location`,
        body: loc ? 'Fixing an address here corrects it for every past and future receipt at this location.' : null,
        fields, submitLabel: loc ? 'Save' : 'Add location',
        onSubmit: (v) => {
          const body = { store_number: v.store_number || null, name: v.name || null, street: v.street || null, city: v.city || null,
            state: v.state || null, postal_code: v.postal_code || null };
          if (loc) {
            if (v.store_chain_id && v.store_chain_id !== chain.id) body.store_chain_id = v.store_chain_id;
            return api('api/v1/store-locations/' + loc.id, { method: 'PATCH', body });
          }
          return api(`api/v1/store-chains/${chain.id}/locations`, { method: 'POST', body });
        },
      });
      if (ok) { toast(loc ? 'Location updated' : 'Location added', 'success'); reload(); }
    }

    // ---------------------------------------------------------------- web lookups (your local search server)
    function hoursText(hours) {
      const keys = Object.keys(hours);
      const span = (v) => (v === true ? 'open 24 hours' : !v.length ? 'closed' : v.map((x) => `${x[0]}–${x[1]}`).join(', '));
      if (keys.length === 1 && keys[0] === 'all') return 'Open daily ' + span(hours.all);
      return DAYS.filter(([k]) => k in hours || 'all' in hours).map(([k, label]) => `${label.slice(0, 3)} ${span(k in hours ? hours[k] : hours.all)}`).join(' · ');
    }

    function suggestionBox(loc) {
      const x = suggestions[loc.id];
      if (!x || x.status === 'DISMISSED') return null;
      if (x.status === 'APPLIED') return loc.opening_hours ? h('div', { class: 'small muted' }, `Hours from ${x.source || 'the web'}`) : null;
      return h('div', { class: 'banner', style: 'margin-top:8px;padding:8px 10px;flex-wrap:wrap' },
        h('div', { class: 'banner-body' }, h('strong', {}, 'Found online: '), h('span', { class: 'banner-text' }, hoursText(x.hours)),
          h('div', { class: 'small muted' }, `${x.source ? x.source + ' · ' : ''}${Math.round((x.confidence || 0) * 100)}% sure${x.note ? ' · ' + x.note : ''}`,
            x.source_url ? h('a', { href: x.source_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, 'Open page') : null)),
        h('div', { class: 'row', style: 'gap:6px' },
          h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: async () => { try { await api(`api/v1/webinfo/hours/${loc.id}/apply`, { method: 'POST', body: {} }); toast('Hours saved', 'success'); reload(); } catch (e) { toast(e.message, 'error'); } } }, 'Use these hours'),
          h('button', { class: 'btn btn-sm', type: 'button', onclick: async () => { try { await api(`api/v1/webinfo/hours/${loc.id}/dismiss`, { method: 'POST', body: {} }); reload(); } catch (e) { toast(e.message, 'error'); } } }, 'No thanks')));
    }

    function webCard() {
      if (!web.configured) return [];
      const pending = Object.values(suggestions).filter((x) => x.status === 'SUGGESTED').length;
      return [h('div', { class: 'card', style: 'margin-bottom:14px' }, h('div', { class: 'card-body stack' },
        h('div', {}, h('strong', {}, 'Store hours from the web'),
          h('div', { class: 'note' }, lookupText || (pending ? `${pending} suggestion${pending === 1 ? '' : 's'} below to review.` : 'Look up opening hours for your stores with web search. Nothing is saved until you accept it.'))),
        h('div', { class: 'row wrap', style: 'gap:8px' },
          h('button', { class: 'btn btn-sm', type: 'button', disabled: lookupText != null, onclick: () => lookupHours(null) }, icon('refresh'), 'Find hours for all stores'),
          isAdmin() ? h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: testConnection }, 'Test connection') : null,
          isAdmin() ? h('a', { class: 'btn btn-sm btn-ghost', href: 'debug.html' }, 'Debug lookups') : null)))];
    }

    // ---------------------------------------------------------------- stores near home (OpenStreetMap)
    const paintNearby = () => $('nearbyBox').replaceChildren(...nearbyCard());
    function nearbyCard() {
      if (!near || !near.enabled) return [];
      const du = near.distance_unit, all = near.stores || [];
      const visible = all.filter((x) => x.status !== 'HIDDEN' || nearShowHidden);
      const hidden = all.filter((x) => x.status === 'HIDDEN').length;
      const added = all.filter((x) => x.status === 'ADDED').length, fresh = all.filter((x) => x.status === 'NEW').length;
      const choices = [...new Set([1, 2, 5, 10, 15, 20, near.default_radius])].filter((r) => r <= near.max_radius).sort((a, b) => a - b);
      const radius = h('select', { class: 'select', style: 'width:auto', 'aria-label': 'How far to look', onchange: (e) => { nearRadius = e.target.value; } },
        ...choices.map((r) => h('option', { value: r, selected: String(r) === String(nearRadius || near.default_radius) ? true : null }, `${r} ${du}`)));
      const summary = near.searched_at
        ? `${fresh} new${added ? ` · ${added} added` : ''} · searched ${new Date(near.searched_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`
        : 'Find grocery stores around your home, including ones you have never shopped at';
      const rows = visible.map(nearbyRow);
      return [h('details', { class: 'card', style: 'margin-bottom:14px', open: nearOpen || nearBusy ? true : null, ontoggle: (e) => { nearOpen = e.target.open; } },
        h('summary', { class: 'card-body', style: 'cursor:pointer' }, h('strong', {}, 'Stores near home'), h('span', { class: 'note', style: 'margin-left:8px' }, summary)),
        h('div', { class: 'card-body stack', style: 'padding-top:0' },
          h('div', { class: 'row wrap', style: 'gap:8px;align-items:center' },
            h('span', { class: 'note' }, 'Within'), radius,
            h('button', { class: 'btn btn-sm', type: 'button', disabled: nearBusy, onclick: () => searchNearby(!!near.searched_at) }, icon('search'), nearBusy ? 'Searching…' : near.searched_at ? 'Search again' : 'Find stores near home')),
          h('p', { class: 'note', style: 'margin:0' }, 'From OpenStreetMap. Add a store to include it in online price checks, deals and the trip planner; it is only used for an item once a price is found online. Only your home\'s rough location (about a kilometre) is sent.'),
          ...(rows.length ? rows : near.searched_at ? [h('p', { class: 'note' }, 'No grocery stores were found within that distance.')] : []),
          hidden ? h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => { nearShowHidden = !nearShowHidden; paintNearby(); } }, nearShowHidden ? 'Don\'t show hidden stores' : `Show ${hidden} hidden store${hidden === 1 ? '' : 's'}`) : null))];
    }
    function nearbyRow(x) {
      const act = (path, done) => async () => {
        try { await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/nearby-stores/${encodeURIComponent(x.id)}/${path}`, { method: 'POST', body: {} }); if (done) toast(done, 'success'); nearOpen = true; await load(); }
        catch (e) { toast(e.message, 'error'); }
      };
      const badge = x.status === 'KNOWN' ? h('span', { class: 'pill ok', style: 'margin-left:6px' }, 'you shop here')
        : x.status === 'ADDED' ? h('span', { class: 'pill info', style: 'margin-left:6px' }, 'added')
        : x.status === 'HIDDEN' ? h('span', { class: 'pill', style: 'margin-left:6px' }, 'hidden') : null;
      const buttons = x.status === 'NEW' ? [h('button', { class: 'btn btn-sm btn-primary', type: 'button', onclick: act('add', `${x.name} added to your stores`) }, icon('plus'), 'Add'),
          h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: act('hide') }, 'Hide')]
        : x.status === 'ADDED' ? [h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: act('remove', `${x.name} removed`) }, 'Remove')]
        : x.status === 'HIDDEN' ? [h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: act('unhide') }, 'Show')] : [];
      return h('div', { class: 'row wrap', style: 'gap:8px;align-items:flex-start;justify-content:space-between;border-top:1px solid var(--line, rgba(127,127,127,.2));padding-top:8px' },
        h('div', { style: 'flex:1;min-width:12rem' },
          h('div', { style: 'font-weight:600' }, x.name, badge),
          h('div', { class: 'small muted' }, [`${x.distance} ${near.distance_unit}`, x.kind, x.chain && x.chain !== x.name ? `part of ${x.chain}` : null, x.status === 'KNOWN' && x.matched_label ? `your ${x.matched_label}` : null].filter(Boolean).join(' · ')),
          x.address ? h('div', { class: 'small muted' }, x.address) : null,
          h('div', { class: 'small muted' }, x.opening_hours ? hoursText(x.opening_hours) : x.opening_hours_raw ? `Hours: ${x.opening_hours_raw}` : 'Hours not known',
            h('a', { href: x.osm_url, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, 'map'),
            x.website ? h('a', { href: x.website, target: '_blank', rel: 'noopener', style: 'margin-left:8px;text-decoration:underline' }, 'website') : null)),
        h('div', { class: 'row', style: 'gap:6px' }, ...buttons));
    }
    async function searchNearby(force) {
      nearBusy = true; nearOpen = true; paintNearby();
      try {
        const body = { force };
        if (nearRadius || near.default_radius) body.radius = Number(nearRadius || near.default_radius);
        const r = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/nearby-stores/search`, { method: 'POST', body });
        const n = r.stores.filter((x) => x.status === 'NEW').length;
        toast(`Found ${r.stores.length} store${r.stores.length === 1 ? '' : 's'}${n ? `, ${n} you don't shop at yet` : ''}${r.hours_suggested ? `; opening hours for ${r.hours_suggested} of your stores to review below` : ''}`, 'success');
        nearBusy = false; await load();
      } catch (e) { nearBusy = false; toast(e.message, 'error'); paintNearby(); }
    }

    async function lookupHours(ids) {
      try {
        const job = await api(`api/v1/homes/${encodeURIComponent(ctx.homeId)}/webinfo/hours`, { method: 'POST', body: { location_ids: ids, force: !!ids } });
        lookupText = 'Starting…'; $('webBox').replaceChildren(...webCard());
        const done = await RPI.followJob(job.id, (j) => { lookupText = `${j.message} (${j.done} of ${j.total})`; $('webBox').replaceChildren(...webCard()); });
        lookupText = null;
        toast(done.error ? done.error : done.results ? `Found hours for ${done.results} store${done.results === 1 ? '' : 's'}` : 'No hours could be found', done.error || !done.results ? 'error' : 'success');
        reload();
      } catch (e) { lookupText = null; toast(e.message, 'error'); $('webBox').replaceChildren(...webCard()); }
    }

    async function testConnection() {
      const body = h('div', { class: 'stack' }, h('p', { class: 'note' }, 'Testing web search and the model…'));
      const dlg = h('dialog', { class: 'formdlg' }, h('div', { class: 'dialog-body' }, h('h2', {}, 'Search server test'), body),
        h('div', { class: 'dialog-actions' }, h('button', { class: 'btn', type: 'button', onclick: () => dlg.close() }, 'Close')));
      dlg.addEventListener('close', () => dlg.remove());
      document.body.append(dlg); dlg.showModal();
      try {
        const r = await api('api/v1/webinfo/test', { method: 'POST', body: {} });
        body.replaceChildren(
          h('div', { class: 'banner ' + (r.ok ? 'ok' : 'danger') }, icon(r.ok ? 'check' : 'alert'), h('div', { class: 'banner-body' }, r.ok ? 'Web search and the model both answered.' : `Problem at the ${r.step} step: ${r.error}`)),
          r.server ? h('p', { class: 'small' }, 'Server: ' + r.server) : null,
          r.hits && r.hits.length ? h('div', { class: 'small' }, h('strong', {}, 'First results: '), ...r.hits.map((x) => h('div', {}, `${x.title || '(no title)'} · ${x.url || '(no address)'}`))) : null,
          r.fetch_preview ? h('p', { class: 'small' }, `Opened ${r.fetch_url ? new URL(r.fetch_url).hostname : 'a page'} (${r.fetch_chars} characters): “${r.fetch_preview}…”`) : null,
          r.fetch_note ? h('p', { class: 'small muted' }, r.fetch_note) : null,
          r.fetch_failures && r.fetch_failures.length ? h('div', { class: 'small muted' }, h('strong', {}, r.fetch_preview ? 'Pages that refused first: ' : 'Why: '),
            ...r.fetch_failures.map((x) => h('div', {}, x))) : null);
      } catch (e) { body.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
    }

    // ---- opening hours: used by the trip planner so it never sends you to a closed store
    const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']];
    const spanText = (spans) => spans.map((x) => `${x[0]}–${x[1]}`).join(', ');
    function hoursSummary(oh) {
      const keys = Object.keys(oh);
      if (keys.length === 1 && keys[0] === 'all' && Array.isArray(oh.all) && oh.all.length === 1) return `Open daily ${spanText(oh.all)}`;
      if (keys.length === 1 && keys[0] === 'all' && oh.all === true) return 'Open 24 hours';
      return 'Opening hours set';
    }

    async function hoursDialog(chain, loc) {
      const current = loc.opening_hours || {};
      const rows = DAYS.map(([key, label]) => {
        const spec = key in current ? current[key] : current.all;
        let state = 'unknown', open = '08:00', close = '20:00', spans = [];
        if (spec === true) state = '24h';
        else if (Array.isArray(spec)) {
          if (!spec.length) state = 'closed';
          else if (spec.length === 1) { state = 'open'; [open, close] = spec[0]; }
          else { state = 'custom'; spans = spec; }
        }
        const sel = h('select', { class: 'select', 'aria-label': `${label} hours`, style: 'width:auto' },
          [['unknown', 'Not set'], ['open', 'Open'], ['24h', '24 hours'], ['closed', 'Closed'], ...(state === 'custom' ? [['custom', spanText(spans)]] : [])].map(([v, t]) => h('option', { value: v }, t)));
        sel.value = state;
        const o = h('input', { class: 'input', type: 'time', value: open, 'aria-label': `${label} opens`, style: 'width:auto' });
        const c = h('input', { class: 'input', type: 'time', value: close, 'aria-label': `${label} closes`, style: 'width:auto' });
        const times = h('span', { class: 'row', style: 'gap:6px' }, o, '–', c);
        const paint = () => { times.hidden = sel.value !== 'open'; };
        sel.addEventListener('change', paint); paint();
        return { key, label, sel, o, c, spans, times };
      });
      const error = h('p', { class: 'form-error', role: 'alert', hidden: true });
      const copy = h('button', { class: 'btn btn-sm', type: 'button', onclick: () => {
        const first = rows[0];
        for (const r of rows.slice(1)) { r.sel.value = first.sel.value === 'custom' ? 'unknown' : first.sel.value; r.o.value = first.o.value; r.c.value = first.c.value; r.sel.dispatchEvent(new Event('change')); }
      } }, 'Copy Monday to every day');
      const save = h('button', { class: 'btn btn-primary', type: 'submit' }, 'Save hours');
      const dlg = h('dialog', { class: 'formdlg' }, h('form', { method: 'dialog' },
        h('div', { class: 'dialog-body' }, h('h2', {}, `Opening hours: ${loc.label}`),
          h('p', {}, 'The trip planner only plans a visit when the store is open. Days left as “Not set” count as open.'),
          ...rows.map((r) => h('div', { class: 'row', style: 'justify-content:space-between;margin:6px 0' }, h('span', { style: 'min-width:86px' }, r.label), r.sel, r.times)),
          h('div', { style: 'margin-top:8px' }, copy), error),
        h('div', { class: 'dialog-actions' }, h('button', { class: 'btn btn-ghost', type: 'button', onclick: () => dlg.close() }, 'Cancel'), save)));
      dlg.querySelector('form').addEventListener('submit', async (e) => {
        e.preventDefault(); error.hidden = true; save.disabled = true;
        const value = {};
        for (const r of rows) {
          if (r.sel.value === 'open') { if (!r.o.value || !r.c.value) { error.textContent = `Set when ${r.label} opens and closes`; error.hidden = false; save.disabled = false; return; } value[r.key] = [[r.o.value, r.c.value]]; }
          else if (r.sel.value === '24h') value[r.key] = true;
          else if (r.sel.value === 'closed') value[r.key] = [];
          else if (r.sel.value === 'custom') value[r.key] = r.spans;
        }
        try {
          await api('api/v1/store-locations/' + loc.id, { method: 'PATCH', body: { opening_hours: Object.keys(value).length ? value : null } });
          dlg.close(); toast('Opening hours saved', 'success'); reload();
        } catch (err) { error.textContent = err.message; error.hidden = false; }
        save.disabled = false;
      });
      dlg.addEventListener('close', () => dlg.remove());
      document.body.append(dlg); dlg.showModal();
    }

    async function mergeLocation(loc, preselect) {
      const options = [];
      for (const c of dir.chains) for (const l of c.locations) if (l.id !== loc.id) options.push({ value: l.id, label: l.label });
      if (!options.length) return toast('There is no other location to merge into', 'error');
      const ok = await formDialog({
        title: `Merge “${loc.label}”`,
        body: 'Its receipts and prices move to the location you pick, and this one is removed. This can’t be undone.',
        fields: [{ key: 'into', label: 'Merge into', type: 'select', options, value: preselect || options[0].value }],
        submitLabel: 'Merge', danger: true,
        onSubmit: (v) => api(`api/v1/store-locations/${loc.id}/merge`, { method: 'POST', body: { into_location_id: v.into } }),
      });
      if (ok) { toast('Locations merged', 'success'); reload(); }
    }

    async function deleteLocation(loc) {
      if (!(await confirmDialog({ title: 'Delete this location?', body: loc.label, confirmLabel: 'Delete', danger: true }))) return;
      try { await api('api/v1/store-locations/' + loc.id, { method: 'DELETE' }); toast('Location deleted'); reload(); } catch (e) { toast(e.message, 'error'); }
    }

    // ---------------------------------------------------------------- start
    async function init() {
      try { ctx = await RPI.homeContext(); }
      catch (e) { return $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
      if (!ctx.homeId) return $('content').replaceChildren(h('div', { class: 'empty' }, icon('home'), h('h2', {}, 'No home yet'), h('p', {}, 'Create a home from the receipts page first.')));
      const sw = RPI.homeSwitcher(ctx.homes, ctx.homeId, (id) => { ctx.homeId = id; paintHome(); load(); });
      $('homeSwitch').replaceChildren(...(sw ? [sw] : []));
      paintHome();
      load();
    }
    function paintHome() {
      const home = ctx.homes.find((x) => x.id === ctx.homeId);
      $('homeLine').textContent = (home ? home.name + ' · ' : '') + 'names, addresses and chains';
    }
    init();
