// whoami.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, api } = RPI;
    const c = document.getElementById('content');

    async function load() {
      let w;
      try { w = await api('api/v1/whoami'); } catch (e) { return c.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
      // common/whoami.js: the same rows and wording as every other app
      c.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body' }, HouseholdWhoami.panel(w, {
        classes: { row: 'kv-row', label: 'k', value: 'v', copy: 'btn btn-ghost btn-sm', advice: 'muted', hint: 'muted' },
        adviceStyle: 'margin-top:12px',
      }))));
    }
    load();
