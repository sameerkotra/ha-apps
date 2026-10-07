// whoami.html: this page's own script (moved out of the page so the Content-Security-Policy can forbid inline scripts).
    const { h, api, toast } = RPI;
    const c = document.getElementById('content');

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
      return h('div', { class: 'card', id: 'assistantCard', style: 'margin-top:12px' }, h('div', { class: 'card-body' },
        h('label', { for: 'assistantOk' }, box, ' Let the Household Assistant answer for me'),
        h('p', { class: 'muted' }, 'When on, the Household Assistant can tell you about the shopping list, prices and spending here.')));
    }

    async function load() {
      let w;
      try { w = await api('api/v1/whoami'); } catch (e) { return c.replaceChildren(h('p', { class: 'form-error' }, e.message)); }
      // common/whoami.js: the same rows and wording as every other app
      c.replaceChildren(h('div', { class: 'card' }, h('div', { class: 'card-body' }, HouseholdWhoami.panel(w, {
        classes: { row: 'kv-row', label: 'k', value: 'v', copy: 'btn btn-ghost btn-sm', advice: 'muted', hint: 'muted' },
        adviceStyle: 'margin-top:12px',
      }))));
      try { const card = assistantCard(await api('api/v1/me')); if (card) c.append(card); } catch (_) { /* the card is optional */ }
    }
    load();
