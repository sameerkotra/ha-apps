// admin.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, toast, confirmDialog } = RPI;
    const $ = (id) => document.getElementById(id);
    let me = null;

    function tab() { return location.hash === '#homes' ? 'homes' : 'settings'; }
    function showTabs() {
      const nav = RPI.adminTabs(tab());
      const old = document.querySelector('.admin-tabs') || $('adminTabs');
      old.replaceWith(nav);
      $('sub').textContent = tab() === 'homes' ? 'Homes and people' : 'App settings';
    }
    window.addEventListener('hashchange', () => { showTabs(); render(); });

    function denied() {
      $('content').replaceChildren(h('div', { class: 'empty' }, icon('info'), h('h2', {}, 'Administrators only'),
        h('p', {}, 'Administrators are the people on the app’s admin_users option (Configuration tab). ',
          h('a', { href: 'whoami.html', style: 'text-decoration:underline' }, 'How the app sees you'))));
    }

    async function render() {
      if (!me) {
        try { me = await RPI.me(); } catch (e) { return $('content').replaceChildren(h('p', { class: 'form-error' }, e.message)); }
      }
      if (!me.is_admin) return denied();
      if (tab() === 'homes') return renderHomes();
      return renderSettings();
    }

    // ---------- App settings: drawn by common/settings.js from the server's description of each setting ----------
    async function renderSettings() {
      $('content').replaceChildren(...[0, 1, 2].map(() => h('div', { class: 'skeleton', style: 'height:160px' })));
      try {
        await SettingsPage.render($('content'), {
          load: () => api('api/admin/settings'),
          save: (body) => api('api/admin/settings', { method: 'PUT', body }),
          classes: { card: 'card sp-card', primary: 'btn btn-primary', secondary: 'btn', ghost: 'btn btn-ghost', input: 'input', select: 'select' },
          intro: 'Settings for everyone using the app. They apply as soon as they are saved. Who is an administrator is set on the app’s Configuration tab (admin_users).',
          savedMessage: 'Settings saved',
          toast: (msg) => toast(msg, 'success'),
        });
      } catch (e) { $('content').replaceChildren(h('p', { class: 'form-error' }, e.message)); }
    }

    // ---------- Homes and people ----------
    async function renderHomes() {
      const c = $('content');
      let hs, users;
      try { [hs, users] = await Promise.all([api('api/v1/homes'), api('api/v1/users')]); }
      catch (e) { return c.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
      const act = async (fn, done) => { try { await fn(); toast(done, 'success'); } catch (e) { toast(e.message, 'error'); } renderHomes(); };
      const newName = h('input', { class: 'input', placeholder: 'New home name', 'aria-label': 'New home name', maxlength: '255' });
      const homeRows = hs.map((x) => {
        const inp = h('input', { class: 'input', value: x.name, 'aria-label': 'Home name', maxlength: '255' });
        const used = x.receipt_count + x.pending_count;
        return h('div', { class: 'mrow' }, inp,
          h('button', { class: 'btn btn-sm', onclick: () => act(() => api('api/v1/homes/' + x.id, { method: 'PATCH', body: { name: inp.value } }), 'Renamed') }, 'Save'),
          h('button', { class: 'btn btn-sm btn-danger', disabled: used > 0, title: used ? 'This home has receipts' : 'Delete home', 'aria-label': 'Delete home',
            onclick: async () => { if (await confirmDialog({ title: `Delete “${x.name}”?`, confirmLabel: 'Delete', danger: true })) act(() => api('api/v1/homes/' + x.id, { method: 'DELETE' }), 'Deleted'); } }, icon('trash')));
      });
      c.replaceChildren(
        h('section', { class: 'card' }, h('div', { class: 'card-body stack' },
          h('div', {}, h('h2', { style: 'font-size:1.02rem' }, 'Homes'),
            h('p', { class: 'muted small' }, 'Receipts, prices and lists are kept per home. Everyone can view and add to every home.')),
          h('div', {}, ...homeRows,
            h('div', { class: 'mrow' }, newName,
              h('button', { class: 'btn btn-sm btn-primary', onclick: () => act(() => api('api/v1/homes', { method: 'POST', body: { name: newName.value } }), 'Home created') }, icon('plus'), 'Add'))))),
        h('section', { class: 'card' }, h('div', { class: 'card-body stack' },
          h('div', {}, h('h2', { style: 'font-size:1.02rem' }, 'People'),
            h('p', { class: 'muted small' }, 'Everyone who has opened the app. Administrators are set with admin_users on the app’s Configuration tab (a Home Assistant user name or user id per line), then a restart.')),
          h('div', { class: 'kv' }, users.map((u) => h('div', { class: 'kv-row' },
            h('span', { class: 'k' }, (u.display_name || u.id) + (u.id === me.id ? ' (you)' : '')),
            h('span', { class: 'v' }, u.is_admin ? 'Administrator' : 'Member')))))));
    }

    showTabs();
    render();
