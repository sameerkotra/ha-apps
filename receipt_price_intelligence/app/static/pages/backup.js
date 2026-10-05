// backup.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, icon, api, toast, confirmDialog } = RPI;
    const $ = (id) => document.getElementById(id);
    $('adminTabs').replaceWith(RPI.adminTabs('backup'));

    const LABELS = [['homes', 'Homes'], ['receipts', 'Receipts'], ['receipt_items', 'Lines'], ['price_observations', 'Prices'],
      ['common_items', 'Items'], ['store_chains', 'Stores'], ['store_locations', 'Locations'], ['users', 'People']];
    const size = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');
    const when = (iso) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    const card = (title, ...body) => h('div', { class: 'card' }, h('div', { class: 'card-head' }, h('span', { class: 'card-title' }, title)), h('div', { class: 'card-body stack' }, ...body));

    let chosen = null;

    async function load() {
      const c = $('content');
      let me, info;
      try { me = await RPI.me(); } catch (e) { return c.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
      if (!me.is_admin) {
        return c.replaceChildren(h('div', { class: 'empty' }, icon('info'), h('h2', {}, 'Administrators only'), h('p', {}, 'A backup contains everyone’s receipts, so only an administrator can export or restore it.')));
      }
      try { info = await api('api/v1/admin/backup/info'); } catch (e) { return c.replaceChildren(h('div', { class: 'banner danger' }, icon('alert'), h('div', { class: 'banner-body' }, e.message))); }
      render(info);
    }

    function render(info) {
      const c = $('content');
      const fileInput = h('input', { type: 'file', accept: '.db,.sqlite,.sqlite3,application/vnd.sqlite3,application/octet-stream', hidden: true });
      const nameEl = h('span', { class: 'filename' }, 'No file chosen');
      const restoreBtn = h('button', { class: 'btn btn-danger', type: 'button', disabled: true, onclick: () => restore(restoreBtn) }, icon('upload'), 'Restore from this file');
      fileInput.addEventListener('change', () => {
        chosen = fileInput.files[0] || null;
        nameEl.textContent = chosen ? `${chosen.name} (${size(chosen.size)})` : 'No file chosen';
        restoreBtn.disabled = !chosen;
      });
      chosen = null;

      c.replaceChildren(
        card('What is stored',
          h('dl', { class: 'counts' }, LABELS.map(([k, l]) => h('div', {}, h('dt', {}, l), h('dd', {}, String(info.counts[k] ?? 0))))),
          h('p', { class: 'small muted' }, `Database size ${size(info.database_bytes)}.`)),
        card('Export',
          h('p', {}, 'Download everything as a single file: every home’s receipts, prices, items, stores and people. Receipt photos are not part of it, because they are deleted once a receipt is saved.'),
          h('div', {}, h('a', { class: 'btn btn-primary', href: RPI.url('api/v1/admin/backup/export'), download: '' }, icon('download'), 'Download backup')),
          h('p', { class: 'small muted' }, 'The file contains personal financial information. Keep it somewhere private.')),
        card('Restore',
          h('p', {}, 'Replace everything in the app with the contents of an exported file. Files from older versions are upgraded automatically.'),
          h('div', { class: 'banner warn' }, icon('alert'), h('div', { class: 'banner-body' }, h('span', { class: 'banner-text' },
            'All current homes, receipts and prices are replaced. A copy of the current data is kept first. App settings come from the file if it has them; otherwise the current ones are kept. Administrators still come from admin_users.'))),
          h('div', { class: 'filebox' }, h('button', { class: 'btn', type: 'button', onclick: () => fileInput.click() }, icon('file'), 'Choose file…'), nameEl, fileInput),
          h('div', {}, restoreBtn),
          h('p', { class: 'small muted' }, `Up to ${info.max_import_mb} MB.`)),
        info.safety_copies.length ? card('Copies from before earlier restores',
          h('p', { class: 'small muted' }, 'Kept automatically so a restore can be undone. The newest three are kept.'),
          h('ul', { class: 'copies' }, info.safety_copies.map((x) => h('li', {},
            h('span', {}, `${when(x.created)} · ${size(x.size)}`),
            h('a', { href: RPI.url('api/v1/admin/backup/safety-copies/' + encodeURIComponent(x.name)), download: '' }, 'Download'))))) : null);
    }

    async function restore(btn) {
      if (!chosen) return;
      const ok = await confirmDialog({
        title: 'Replace everything with this file?',
        body: `“${chosen.name}” will replace all homes, receipts and prices in the app. A copy of the current data is kept first.`,
        confirmLabel: 'Replace everything', danger: true,
      });
      if (!ok) return;
      btn.disabled = true;
      const overlay = h('dialog', { class: 'formdlg' }, h('div', { class: 'dialog-body' }, h('h2', {}, 'Restoring…'), h('p', {}, 'Checking the file and replacing the data. This can take a moment. Please keep this page open.')));
      document.body.append(overlay); overlay.showModal();
      try {
        const form = new FormData();
        form.append('file', chosen, chosen.name);
        const res = await api('api/v1/admin/backup/import', { method: 'POST', form });
        overlay.close(); overlay.remove();
        showDone(res);
      } catch (e) {
        overlay.close(); overlay.remove();
        btn.disabled = false;
        toast(e.message, 'error');
      }
    }

    function showDone(res) {
      const c = $('content');
      c.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body stack' },
        h('div', { class: 'banner ok' }, icon('check'), h('div', { class: 'banner-body' }, h('strong', {}, 'Restored. '), h('span', { class: 'banner-text' },
          `${res.imported.receipts} receipts, ${res.imported.price_observations} prices and ${res.imported.homes} home${res.imported.homes === 1 ? '' : 's'} are now in the app.`))),
        h('p', { class: 'small muted' }, 'A copy of what was here before is kept and can be downloaded from this page.'),
        h('div', { class: 'row' }, h('a', { class: 'btn btn-primary', href: 'index.html' }, 'Open receipts'), h('button', { class: 'btn', type: 'button', onclick: load }, 'Back to backup')))));
    }

    load();
