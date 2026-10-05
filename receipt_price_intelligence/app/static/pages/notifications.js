// notifications.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, toast } = RPI;
    const $ = (id) => document.getElementById(id);
    let info = null, s = null, dirty = false, saving = false;

    const KIND_TEXT = {
      alerts: 'Price targets you set, new lows, deals on items you buy.',
      price_drop: 'An item on your shopping list is cheaper somewhere than you usually pay.',
      restock: 'Once a day: items you are due to buy again, from how often you buy them.',
      overcharge: 'After you approve a receipt: lines that cost more than the store posted that week.',
      nearby: 'When one of your phones is at a store where items on your shopping list are cheapest (not held back by quiet hours).',
    };

    async function load() {
      try { info = await api('api/v1/notifications'); s = JSON.parse(JSON.stringify(info.settings)); dirty = false; render(); }
      catch (e) { $('content').replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
    }
    const change = (fn) => { fn(); dirty = true; render(); };

    async function test(service) {
      try { const r = await api('api/v1/notifications/test', { method: 'POST', body: { service: service || null } }); toast(`Test sent (${r.sent})`, 'success'); }
      catch (e) { toast(e.message, 'error'); }
    }
    async function save() {
      saving = true; render();
      try {
        s = await api('api/v1/notifications', { method: 'PUT', body: {
          devices: s.devices, bell: s.bell, kinds: s.kinds, price_drop_percent: Number(s.price_drop_percent), restock_auto_add: s.restock_auto_add,
          tracker: s.tracker || null, nearby_meters: Number(s.nearby_meters), quiet: s.quiet } });
        dirty = false; toast('Notification settings saved', 'success');
      } catch (e) { toast(e.message, 'error'); }
      saving = false; render();
    }

    function whereCard() {
      const ha = info.ha;
      if (!ha.available) return h('div', { class: 'banner warn' }, icon('alert'), h('div', { class: 'banner-body' }, 'Home Assistant is not available to the app, so notifications cannot be sent.'));
      const phones = ha.devices.filter((d) => d.kind === 'phone'), other = ha.devices.filter((d) => d.kind !== 'phone');
      const row = (d) => h('label', { class: 'opt' },
        h('input', { type: 'checkbox', checked: s.devices.includes(d.service) ? true : null,
          onchange: (e) => change(() => { s.devices = e.target.checked ? [...s.devices, d.service] : s.devices.filter((x) => x !== d.service); }) }),
        h('div', { class: 'body' }, h('div', { class: 'title' }, d.name), h('div', { class: 'sub' }, d.kind === 'phone' ? 'Push notification to this phone (Home Assistant app)' : d.service)),
        h('button', { class: 'btn btn-sm test', type: 'button', onclick: (e) => { e.preventDefault(); test(d.service); } }, 'Send test'));
      const missing = s.devices.filter((x) => !ha.devices.some((d) => d.service === x));
      return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('span', { class: 'card-title' }, 'Where to send')),
        ha.error ? h('div', { class: 'opt' }, h('span', { class: 'sub' }, `Could not list your devices: ${ha.error}`)) : null,
        ...(phones.length ? phones.map(row) : [h('div', { class: 'opt' }, h('div', { class: 'body' }, h('div', { class: 'title' }, 'No phones found'),
          h('div', { class: 'sub' }, 'Install the Home Assistant app on your phone and sign in: it then appears here for push notifications.')))]),
        ...other.map(row),
        ...missing.map((x) => h('div', { class: 'opt' }, h('div', { class: 'body' }, h('div', { class: 'title' }, x), h('div', { class: 'sub' }, 'Chosen, but Home Assistant does not offer it now')),
          h('button', { class: 'btn btn-sm btn-ghost', type: 'button', onclick: () => change(() => { s.devices = s.devices.filter((y) => y !== x); }) }, 'Remove'))),
        h('label', { class: 'opt' }, h('input', { type: 'checkbox', checked: s.bell ? true : null, onchange: (e) => change(() => { s.bell = e.target.checked; }) }),
          h('div', { class: 'body' }, h('div', { class: 'title' }, 'Home Assistant notifications (the bell)'),
            h('div', { class: 'sub' }, s.devices.length ? 'As well as on the phones chosen' : 'Used whenever no phone is chosen'))));
    }

    function kindRow(k) {
      const on = !!s.kinds[k.key];
      let more = null;
      if (k.key === 'price_drop') more = h('div', { class: 'more' }, 'When at least',
        h('input', { class: 'input', type: 'number', min: 1, max: 90, value: s.price_drop_percent, style: 'width:5rem', oninput: (e) => { s.price_drop_percent = e.target.value; dirty = true; paintSave(); } }), '% cheaper than usual');
      if (k.key === 'restock') more = h('div', { class: 'more' }, h('label', { style: 'display:flex;gap:6px;align-items:center' },
        h('input', { type: 'checkbox', checked: s.restock_auto_add ? true : null, onchange: (e) => change(() => { s.restock_auto_add = e.target.checked; }) }), 'Also add them to the shopping list'));
      if (k.key === 'nearby') {
        const trackers = info.ha.trackers || [], locations = info.ha.locations || {};
        const phones = s.devices.filter((d) => d.startsWith('notify.mobile_app_'));
        const nameOf = (svc) => (info.ha.devices.find((d) => d.service === svc) || { name: svc.replace('notify.mobile_app_', '') }).name;
        const follows = phones.length
          ? h('div', { class: 'more', style: 'display:grid;gap:2px' }, h('span', {}, 'Follows each phone you send to, and tells only the phone at the store:'),
              ...phones.map((p) => h('span', { class: 'sub' }, `• ${nameOf(p)}: `, locations[p] ? `location from ${locations[p]}` : h('b', { style: 'color:var(--warn, #b7791f)' }, 'no location found (turn on location for the Home Assistant app on it)'))))
          : h('div', { class: 'more' }, 'No phone is chosen above, so follow',
              h('select', { class: 'select', onchange: (e) => change(() => { s.tracker = e.target.value || null; }) },
                h('option', { value: '' }, trackers.length ? 'choose a person or phone' : 'no person or phone reports a position'),
                ...trackers.map((t) => h('option', { value: t.entity_id, selected: t.entity_id === s.tracker ? true : null }, t.name)),
                ...(s.tracker && !trackers.some((t) => t.entity_id === s.tracker) ? [h('option', { value: s.tracker, selected: true }, s.tracker)] : [])));
        more = h('div', {}, follows,
          h('div', { class: 'more' }, 'Within', h('input', { class: 'input', type: 'number', min: 50, max: 3000, step: 50, value: s.nearby_meters, style: 'width:6rem', oninput: (e) => { s.nearby_meters = e.target.value; dirty = true; paintSave(); } }), 'm of the store'),
          h('div', { class: 'sub', style: 'margin-top:4px' }, 'Only items on your shopping list, not yet ticked off, for which that store is the cheapest.'));
      }
      return h('div', { class: 'opt' + (on ? '' : ' kind-off') },
        h('input', { type: 'checkbox', checked: on ? true : null, 'aria-label': k.name, onchange: (e) => change(() => { s.kinds[k.key] = e.target.checked; }) }),
        h('div', { class: 'body' }, h('div', { class: 'title' }, k.name), h('div', { class: 'sub' }, KIND_TEXT[k.key] || ''), more));
    }
    function kindsCard() {
      return h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('span', { class: 'card-title' }, 'Which to send')), ...info.kinds.map(kindRow));
    }
    function quietCard() {
      const on = !!s.quiet;
      return h('div', { class: 'card' }, h('div', { class: 'opt' + (on ? '' : ' kind-off') },
        h('input', { type: 'checkbox', checked: on ? true : null, 'aria-label': 'Quiet hours', onchange: (e) => change(() => { s.quiet = e.target.checked ? { start: '22:00', end: '07:00' } : null; }) }),
        h('div', { class: 'body' }, h('div', { class: 'title' }, 'Quiet hours'), h('div', { class: 'sub' }, 'Nothing is sent in these hours, except "cheapest here".'),
          h('div', { class: 'more' }, 'From', h('input', { class: 'input', type: 'time', value: (s.quiet || {}).start || '22:00', onchange: (e) => change(() => { s.quiet = { ...(s.quiet || {}), start: e.target.value }; }) }),
            'to', h('input', { class: 'input', type: 'time', value: (s.quiet || {}).end || '07:00', onchange: (e) => change(() => { s.quiet = { ...(s.quiet || {}), end: e.target.value }; }) })))));
    }
    function saveBar() {
      return h('div', { class: 'savebar', id: 'saveBar' },
        h('span', { class: 'note' }, dirty ? 'Unsaved changes' : (s.saved ? 'Saved' : 'Using the App settings until you save here')),
        h('button', { class: 'btn btn-sm', type: 'button', onclick: () => test(null), disabled: !info.ha.available }, icon('bell'), 'Test'),
        h('button', { class: 'btn btn-primary', type: 'button', disabled: !dirty || saving, onclick: save }, saving ? 'Saving…' : 'Save'));
    }
    function paintSave() { const bar = $('saveBar'); if (bar) bar.replaceWith(saveBar()); }
    function render() {
      $('content').replaceChildren(whereCard(), kindsCard(), quietCard(),
        h('p', { class: 'note' }, 'These settings take precedence over the notification defaults in Admin → App settings, which are used until you save here.'),
        saveBar());
    }
    window.addEventListener('beforeunload', (e) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
    load();
