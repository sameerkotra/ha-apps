// whoami.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, api, toast } = RPI;
    const c = document.getElementById('content');
    document.getElementById('backLink').replaceWith(RPI.backButton('Back', 'list.html'));

    // The person's own "Let the Household Assistant answer for me" — shown only while the admin lets it ask.
    function assistantCard(me) {
      if (!me.assistant) return null;
      const box = h('input', { type: 'checkbox', id: 'assistantOk', checked: !!me.assistantOk });
      box.addEventListener('change', async () => {
        box.disabled = true;
        try {
          const r = await api('api/v1/me/assistant', { method: 'PUT', body: { assistantOk: box.checked } });
          box.checked = !!r.assistantOk;
        } catch (e) { box.checked = !box.checked; toast(e.message, 'error'); }
        box.disabled = false;
      });
      // a switch row: the words on the left, the switch on the right
      return h('div', { class: 'card', id: 'assistantCard' }, h('label', { class: 'card-body switchrow', for: 'assistantOk' },
        h('span', { class: 'grow' }, h('strong', {}, 'Let the Household Assistant answer for me'),
          h('span', { class: 'muted small', style: 'display:block' }, 'When on, the Household Assistant can tell you about the shopping list, prices and spending here.')),
        h('span', { class: 'sp-switch' }, box, h('span', { class: 'sp-track' }, h('span', { class: 'sp-thumb' })))));
    }

    async function load() {
      let w, me = null;
      UI.loading(c);
      try { [w, me] = await Promise.all([api('api/v1/whoami'), api('api/v1/me').catch(() => null)]); } catch (e) { return c.replaceChildren(UI.errorBox(e, load)); }
      // the assistant switch first (when the admin lets it ask), then common/whoami.js: the same rows and wording as every other app
      c.replaceChildren(...[me ? assistantCard(me) : null, h('div', { class: 'card' }, h('div', { class: 'card-body' }, HouseholdWhoami.panel(w, {
        classes: { row: 'kv-row', label: 'k', value: 'v', copy: 'btn btn-ghost btn-sm', advice: 'muted', hint: 'muted' },
        adviceStyle: 'margin-top:12px',
      })))].filter(Boolean));
    }
    load();
