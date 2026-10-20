const state = {
  users: [],
  groups: [],
  currentGroupId: null,
  currentGroup: null,
  selectedMembersForNewGroup: new Set(),
  splitType: 'equal',
  customMode: 'amount',   // Custom split: 'amount' | 'percent'
  splitParticipants: new Set(),
  isAdmin: false,
  sensorSyncEnabled: false,
  haConnected: false,
  currency: 'USD',
  adminTab: 'settings',
  adminUsers: [],
  editingExpenseId: null,
  editingPaymentId: null, // a settle-up payment being changed in its ledger row
  ledgerLoading: false,   // a "Load more" request is on its way
  me: null,               // the Splitpot person Home Assistant links to the signed-in login (from /whoami)
};

// The group ledger loads a page at a time (newest first): LEDGER_PAGE entries when the group opens, then
// LEDGER_PAGE more per "Load more". Balances, counts and the CSV export always come from the server's view of
// every entry, never from the loaded page.
const LEDGER_PAGE = 20;
const LEDGER_MAX = 500;   // the server's largest page

const { $, $$ } = UI;

// api(path, fetchOptions): path is under api/ (e.g. '/groups'); the options go to fetch() as they are.
const api = UI.makeApi({
  url: (path) => 'api' + path,
  init: (options) => ({ headers: { 'Content-Type': 'application/json' }, ...options }),
  networkError: null,
  message: (body) => (body || {}).detail || (body || {}).error || 'Something went wrong',
  makeError: (msg) => new Error(msg),
});

function fmt(n) {
  return (Math.round(n * 100) / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// An amount in the App settings currency (e.g. "$12.50", "€12.50"). Falls
// back to "12.50 XYZ" if the browser doesn't know the code.
function money(n, currency = state.currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(Math.round(n * 100) / 100);
  } catch (e) {
    return `${fmt(n)} ${currency}`;
  }
}

// Re-read the currency (GET /config) so a change an admin made in App
// settings shows up on the next Summary/group load, without reloading.
async function loadConfig() {
  try {
    const cfg = await api('/config');
    state.currency = cfg.currency || state.currency;
    state.page = cfg.page || null;              // the sidebar page: links open a group there (deeplink.js)
  } catch (e) { /* keep the current one */ }
}

// Expense dates are either a full timestamp or a bare YYYY-MM-DD (a day
// picked in the date field, meaning that calendar day locally). new Date()
// would read the bare form as UTC midnight — the previous evening in the
// Americas — so parse it as a local date instead.
function parseWhen(value) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : new Date(value);
}

function localDateInputValue(d = new Date()) {
  const pad = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// ---------- theme switcher and sidebar collapse ----------
// common/theme-boot.js (in <head>) already applied the saved theme and the
// collapsed state before first paint; this wires the <select> and the toggle.
HouseholdTheme.bindSelect(document.getElementById('theme-select'));
HouseholdTheme.bindSelect(document.getElementById('theme-select-m'));   // ⋯ More on a phone
(function () {
  const btn = document.getElementById('sidebarCollapseBtn');
  if (!btn) return;

  function syncButton() {
    const collapsed = HouseholdTheme.sidebarCollapsed();
    btn.textContent = collapsed ? '›' : '‹';
    btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    btn.setAttribute('aria-label', btn.title);
  }

  btn.addEventListener('click', () => {
    HouseholdTheme.setSidebarCollapsed(!HouseholdTheme.sidebarCollapsed());
    syncButton();
  });

  syncButton();
})();

// ---------- navigation ----------
// Every page has an address, so a reload keeps your place and Back retraces your steps (backnav.js):
//   #/groups  #/g/<id>  #/summary  #/activity  #/settings (My settings)  #/whoami  #/admin/<tab>
// Older and short forms still work: #/group/<id> (notification links) opens a group; #/people, #/users and
// #/storage open the matching Admin tab; #/me is My settings.
const ADMIN_TABS = ['settings', 'users', 'storage'];
const SHORT_ADMIN_ROUTES = { '#/people': 'users', '#/users': 'users', '#/storage': 'storage' };

// A group's address: #/g/<id> (and the older #/group/<id>).
function groupFromHash(hash) {
  const m = /^#\/g(?:roup)?\/([\w-]+)$/.exec(hash || '');
  return m ? m[1] : null;
}

function adminTabFromHash(hash) {
  if (Object.prototype.hasOwnProperty.call(SHORT_ADMIN_ROUTES, hash)) return SHORT_ADMIN_ROUTES[hash];
  const m = /^#\/admin(?:\/([\w-]*))?\/?$/.exec(hash);
  if (!m) return null;
  return ADMIN_TABS.includes(m[1]) ? m[1] : 'settings';
}

// The top-level pages (the menu's): going to one starts a new Back trail.
function isTabHash(hash) {
  return /^#\/(groups|summary|activity|settings|me|admin(\/[\w-]*)?)$/.test(hash || '') ||
    Object.prototype.hasOwnProperty.call(SHORT_ADMIN_ROUTES, hash);
}

// Where Back goes from a page opened by a link (nothing before it in the trail).
function parentOfHash(hash) {
  if (groupFromHash(hash)) return '#/groups';
  if (hash === '#/whoami') return '#/settings';
  return null;
}

// Go to an address (backnav.js remembers where you were; { replace: true } doesn't). route() then shows it.
function nav(hash, opts) {
  if (window.BackNav) window.BackNav.go(hash, opts);
  else if (location.hash !== hash) location.hash = hash;
  else route();
}

function openGroup(id) { nav(`#/g/${id}`); }

// Show the page for the current address.
function route() {
  const hash = location.hash || '';
  const tab = adminTabFromHash(hash);
  if (tab) {
    if (hash !== `#/admin/${tab}`) nav(`#/admin/${tab}`, { replace: true });
    else showAdmin(tab);
    return;
  }
  const gid = groupFromHash(hash);
  if (gid) {
    if (hash !== `#/g/${gid}`) nav(`#/g/${gid}`, { replace: true });
    else showGroup(gid);
    return;
  }
  switch (hash) {
    case '#/groups': showGroupsPage(); return;
    case '#/summary': showSummary(); return;
    case '#/activity': showActivity(); return;
    case '#/settings': showMe(); return;
    case '#/me': nav('#/settings', { replace: true }); return;
    case '#/whoami': showWhoami(); return;
    default: goStartPage();        // no address (or an unknown one): the start page
  }
}

// The menu: Groups (with your groups under it on a computer), Summary (Spending and Activity), and — at the bottom
// of the sidebar, or under ⋯ More on a phone — My settings and Admin.
const TAB_OF_VIEW = { groups: 'groups', group: 'groups', summary: 'summary', activity: 'summary', me: 'me', admin: 'admin' };

function showView(name) {
  $$('.view').forEach((v) => v.classList.remove('active'));
  $$('.tab').forEach((t) => { t.classList.remove('active'); t.removeAttribute('aria-current'); });
  const tab = TAB_OF_VIEW[name];
  if (tab) {
    const t = $(`.tab[data-view="${tab}"]`);
    t.classList.add('active');
    t.setAttribute('aria-current', 'page');
  }
  $('#moreBtn').classList.toggle('active', tab === 'me' || tab === 'admin');
  closeMore();
  closeGroupMenu();
  $$('#navGroups .nav-group').forEach((b) => {
    const on = name === 'group' && b.dataset.group === state.currentGroupId;
    b.classList.toggle('active', on);
    if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $$('[data-sumview]').forEach((b) => {
    const on = b.dataset.sumview === (name === 'activity' ? 'activity' : 'spending');
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  // ＋ Add expense: everywhere but the settings pages (on a group's page it starts in that group)
  $('#quickAddBtn').hidden = ['me', 'admin', 'whoami'].includes(name);
  $(`#view-${name}`).classList.add('active');
}

$('#tabs').addEventListener('click', (e) => {
  const grp = e.target.closest('.nav-group');
  if (grp) { openGroup(grp.dataset.group); return; }
  const btn = e.target.closest('.tab');
  if (!btn || btn.id === 'moreBtn') return;
  const view = btn.dataset.view;
  if (view === 'admin') openAdmin(state.adminTab);
  else if (view === 'me') nav('#/settings');
  else nav(`#/${view}`);
});

// ⋯ More (phones): My settings, Admin and the theme in a small panel above the bottom bar
function closeMore() {
  $('#tabs').classList.remove('more-open');
  $('#moreBtn').setAttribute('aria-expanded', 'false');
}
$('#moreBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  const open = !$('#tabs').classList.contains('more-open');
  $('#tabs').classList.toggle('more-open', open);
  $('#moreBtn').setAttribute('aria-expanded', open ? 'true' : 'false');
});
document.addEventListener('click', (e) => { if (!e.target.closest('#navMore')) closeMore(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#tabs').classList.contains('more-open')) { closeMore(); $('#moreBtn').focus(); } });

// Summary's two views (in-page tabs: Back doesn't step through them)
$$('[data-sumview]').forEach((b) => b.addEventListener('click', () => {
  nav(b.dataset.sumview === 'activity' ? '#/activity' : '#/summary', { replace: true });
}));

// Your groups under Groups in the sidebar (a computer; the phone's bar has no room)
function renderNavGroups() {
  $('#navGroups').innerHTML = sortedGroups().map((g) => `<button type="button" class="nav-group${g.id === state.currentGroupId && $('#view-group').classList.contains('active') ? ' active' : ''}" data-group="${escapeHtml(g.id)}" title="${escapeHtml(g.name)}">${g.isDefault ? '<span class="nav-group-star" aria-label="your default">☆</span>' : ''}<span class="nav-group-name">${escapeHtml(g.name)}</span></button>`).join('');
}

// Every address change (a link, BackNav.go, Back, a typed address) shows its page.
window.addEventListener('hashchange', route);

// ← All groups: back where you came from (the Groups list when there's nothing before)
$('#backToGroups').addEventListener('click', () => {
  if (!(window.BackNav && window.BackNav.back())) nav('#/groups');
});

// ---------- dialogs (common/ui.js) ----------
// Every dialog opens in #modalRoot and is a layer for Back (closed before Back leaves the page).
const openModals = [];   // their close functions, oldest first
function modalOptions(extra) {
  return Object.assign({
    root: '#modalRoot',
    onOpen: (close) => { openModals.push(close); },
    onClosed: (close) => { const i = openModals.indexOf(close); if (i >= 0) openModals.splice(i, 1); },
  }, extra || {});
}
function modal(title, content, extra) {
  return UI.openModal(title, content, modalOptions(extra));
}
// "Delete …?" and the like: → true when confirmed
function confirmAction(title, message, okLabel = 'OK', danger = true) {
  return UI.confirmDialog(title, message, {
    okLabel, okClass: danger ? 'btn danger' : 'btn primary', cancelClass: 'btn ghost', focusOk: !danger,
    modal: modalOptions(),
  });
}

// ---------- "How the app sees you" ----------
// Read-only diagnostic: exactly which user name / id Home Assistant sent, and
// whether they match the admin_users list. Reached from the user chip in the
// sidebar and the 👤 button in the top bar (phones have no sidebar footer).
function openWhoami() { nav('#/whoami'); }
function showWhoami() {
  showView('whoami');
  loadWhoami();
}

async function loadWhoami() {
  const body = $('#whoamiBody');
  UI.loading(body);
  let w;
  try {
    w = await api('/whoami');
  } catch (e) {
    UI.mount(body, UI.errorBox(e, loadWhoami));
    return;
  }
  // common/whoami.js: the same rows and wording as every other app
  body.replaceChildren(HouseholdWhoami.panel(w, {
    appName: 'Splitpot',
    classes: { copy: 'btn ghost kv-copy' },
  }));
}

$('#sidebarUser').addEventListener('click', openWhoami);
$('#myPrefsWhoami').addEventListener('click', openWhoami);

// ---------- My settings (each person's own choices) ----------
// Matched to you by your Home Assistant login, never by name. Today: whether you get phone notifications.
function showMe() {
  showView('me');
  loadMyPrefs();
}

async function loadMyPrefs() {
  const body = $('#myPrefsBody');
  UI.loading(body);
  let p;
  try {
    p = await api('/me/prefs');
  } catch (err) {
    UI.mount(body, UI.errorBox(err, loadMyPrefs));
    return;
  }
  renderMyPrefs(p);
}

function renderMyPrefs(p) {
  const h = UI.h;
  const save = async (on, input) => {
    try {
      const next = await api('/me/prefs', { method: 'PUT', body: JSON.stringify({ receiveNotifications: on }) });
      toast(on ? 'You\'ll get notifications' : 'Notifications off for you');
      renderMyPrefs(next);
    } catch (err) {
      input.checked = !on;
      toast(err.message, true);
    }
  };
  let status = null;
  if (!p.linked) {
    status = 'Home Assistant doesn\'t link your login to a person in Splitpot yet, so there is nothing to set here. In Home Assistant: Settings → People → you → Allow person to login.';
  } else if (!p.notifyCharges) {
    status = 'Phone notifications are switched off for the whole household — an admin can switch them on in Admin → App settings.';
  } else if (!p.reachable) {
    status = 'No phone is linked to you in Home Assistant yet (Settings → People → you → Track device, with the Companion app), so nothing can reach you.';
  }
  const what = p.notifyPayments
    ? 'A phone notification when someone else adds a charge you paid for or have a share in, and when a settle-up payment to or from you is recorded.'
    : 'A phone notification when someone else adds a charge you paid for or have a share in.';
  UI.mount($('#myPrefsBody'),
    h('div', { class: 'sp-card my-prefs', id: 'myPrefsCard' },
      h('h2', { class: 'first' }, 'Notifications'),
      h('div', { class: 'pref-row' },
        h('div', null, h('div', { class: 'pref-label' }, 'Receive notifications'), h('div', { class: 'hint' }, what)),
        PeoplePage.accessSwitch(p.linked ? p.receiveNotifications : false, save,
          { label: 'Receive notifications', disabled: !p.linked })),
      status ? h('p', { class: 'field-help warn', id: 'myPrefsStatus' }, status) : null),
    p.assistant ? h('div', { class: 'sp-card my-prefs', id: 'myAssistantCard' },
      h('h2', { class: 'first' }, 'Household Assistant'),
      h('div', { class: 'pref-row' },
        h('div', null, h('div', { class: 'pref-label' }, 'Let the Household Assistant answer for me'),
          h('div', { class: 'hint' }, 'When you ask the Household Assistant app, it can tell you the balances and recent expenses of the groups you are in.')),
        PeoplePage.accessSwitch(p.linked ? p.assistantOk : false, saveAssistant,
          { label: 'Let the Household Assistant answer for me', disabled: !p.linked }))) : null);
}

async function saveAssistant(on, input) {
  try {
    const next = await api('/me/prefs', { method: 'PUT', body: JSON.stringify({ assistantOk: on }) });
    toast(on ? 'The Household Assistant can answer for you' : 'The Household Assistant won\'t answer for you');
    renderMyPrefs(next);
  } catch (err) {
    input.checked = !on;
    toast(err.message, true);
  }
}
$('#whoamiBtn').addEventListener('click', openWhoami);

// ---------- users (picker list, for everyone) ----------
async function loadUsers() {
  state.users = await api('/users');
  renderActingAs();
}

// ---------- Admin area ----------
function openAdmin(tab = 'settings', opts) {
  nav(`#/admin/${ADMIN_TABS.includes(tab) ? tab : 'settings'}`, opts);
}

// Shown by route() for #/admin/<tab>
function showAdmin(tab = 'settings') {
  if (!ADMIN_TABS.includes(tab)) tab = 'settings';
  showView('admin');
  // The server refuses every admin API for everyone else anyway; this just
  // shows a clear message instead of a broken page.
  $('#adminDenied').hidden = state.isAdmin;
  $('#adminBody').hidden = !state.isAdmin;
  if (!state.isAdmin) return;
  state.adminTab = tab;
  $$('.admin-tab').forEach((b) => {
    const on = b.dataset.adminTab === tab;
    b.classList.toggle('active', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  ADMIN_TABS.forEach((t) => { $(`#admin-${t}`).hidden = t !== tab; });
  $$('.admin-tab').forEach((b) => { if (b.dataset.adminTab === tab) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  if (tab === 'settings') loadSettings();
  if (tab === 'users') loadAdminUsers();
}

$('.admin-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-admin-tab]');
  if (btn) openAdmin(btn.dataset.adminTab, { replace: true });   // a sub-tab: not a step for Back
});
$('#adminDeniedWhoami').addEventListener('click', openWhoami);

// -- App settings --
// Drawn by common/settings.js from the server's description of each setting; this app adds the
// currency preview and the "no Home Assistant connection" note.
async function loadSettings() {
  const box = $('#appSettingsBox');
  const h = UI.h;
  UI.loading(box);
  try {
    await SettingsPage.render(box, {
      load: () => api('/admin/settings'),
      save: (body) => api('/admin/settings', { method: 'PUT', body: JSON.stringify(body) }),
      classes: { card: 'sp-card', primary: 'btn primary', secondary: 'btn ghost', ghost: 'btn ghost' },
      saveLabel: 'Save settings',
      fields: {
        currency: {
          after: (page) => {
            const out = h('strong', { id: 'setCurrencyPreview' });
            const paint = () => {
              const code = String(page.value('currency') || '').trim().toUpperCase();
              out.textContent = /^[A-Z]{3}$/.test(code) ? money(1234.5, code) : '—';
            };
            paint();
            page.box.oninput = paint;          // one listener, replaced on every redraw
            return h('div', { class: 'sp-help' }, 'Looks like: ', out);
          },
        },
        ha_sync_enabled: {
          after: () => (state.haConnected ? null : h('div', { class: 'field-help warn', id: 'syncNoHaNote' },
            'Splitpot has no Home Assistant connection right now, so nothing is published even while this is on.')),
        },
      },
      afterSave: (s) => {
        state.currency = s.values.currency;   // every amount rendered from now on uses it
        state.sensorSyncEnabled = state.haConnected && !!s.values.ha_sync_enabled;
        return 'Saved.';
      },
    });
  } catch (err) {
    UI.mount(box, UI.errorBox(err, loadSettings));
  }
}

// -- Users --
// refresh: "Check Home Assistant again" (reads HA's people and their phones now).
async function loadAdminUsers(refresh = false) {
  const errBox = $('#usersError');
  errBox.textContent = '';
  if (!state.adminUsers.length) UI.loading($('#usersList'));
  try {
    state.adminUsers = await api(`/admin/users${refresh ? '?refresh=1' : ''}`);
  } catch (err) {
    UI.mount($('#usersList'), UI.errorBox(err, () => loadAdminUsers(refresh)));
    return;
  }
  // The notify list comes from Home Assistant; the page still works without it.
  try {
    state.notifyServices = await api(`/admin/notify-services${refresh ? '?refresh=1' : ''}`);
  } catch (err) {
    state.notifyServices = { available: false, services: [], entities: [], error: err.message };
  }
  renderAdminUsers();
}

// A short message at the bottom of the screen (common/ui.js; styled in style.css).
function toast(msg, isError) {
  UI.toast(msg, { error: !!isError, role: isError ? 'alert' : 'status' });
}

// The shared people list (common/people.js): each person with Enable / Disable, their phones from Home Assistant
// and extra notify services (with Send a test).
function renderAdminUsers() {
  const h = UI.h;
  PeoplePage.render($('#usersList'), {
    people: state.adminUsers,
    cardClass: 'sp-card',
    checkAgain: async () => { await loadAdminUsers(true); },
    empty: 'No Home Assistant users found yet — add people under Settings → People in Home Assistant.',
    notify: {
      api: (path, opts = {}) => api(path.replace(/^api/, ''), opts.body !== undefined && typeof opts.body !== 'string'
        ? { ...opts, body: JSON.stringify(opts.body) } : opts),
      services: state.notifyServices || { available: false, services: [], entities: [], error: null },
      toast,
      fail: (err) => toast(err.message, true),
      buttonClass: 'btn ghost',
      ghostClass: 'btn ghost',
      path: (u) => `api/admin/users/${encodeURIComponent(u.id)}/notify`,
      testPath: (u) => `api/admin/users/${encodeURIComponent(u.id)}/notify/test`,
      retry: () => loadAdminUsers(true),
      extraLine: (u) => (u.receiveNotifications ? null : h('span', { class: 'pp-hint' }, ' · turned notifications off')),
      texts: { none: (u, phones) => (phones ? 'None' : 'None — no notifications until a phone is linked in Home Assistant or a service is added here.') },
    },
    person: (u) => {
      const groups = `In ${u.groupCount} group${u.groupCount === 1 ? '' : 's'}`;
      return {
        badges: [u.disabled ? ['Disabled', 'warn'] : null],
        sub: h('span', null, u.disabled ? `${groups} · Disabled — hidden from selection` : groups,
          u.haEntityId ? [' · ', h('code', null, u.haEntityId)] : null),
        controls: h('button', { class: 'btn ghost', type: 'button', onclick: async () => {
          const errBox = $('#usersError');
          errBox.textContent = '';
          try {
            await api(`/users/${encodeURIComponent(u.id)}`, { method: 'PATCH', body: JSON.stringify({ disabled: !u.disabled }) });
            await loadAdminUsers();
            await loadUsers();   // the pickers
          } catch (err) {
            errBox.textContent = err.message;
          }
        } }, u.disabled ? 'Enable' : 'Disable'),
      };
    },
  });
}

// Acting as (top right, admins only: logging something on someone else's behalf). Everyone else is "Paid by"
// themselves by default; the hidden select still holds who that is.
function renderActingAs() {
  const sel = $('#actingAs');
  const prev = sel.value;
  const selectable = state.users.filter((u) => !u.disabled);
  sel.innerHTML = '<option value="">—</option>' + selectable.map((u) => `<option value="${u.id}">${escapeHtml(u.name)}</option>`).join('');
  if (selectable.some((u) => u.id === prev)) sel.value = prev;
}

// "Paid by" starts as the "Acting as" person (the signed-in Home Assistant
// user by default) when they're an enabled member of the open group;
// otherwise it's left as is. Never while an expense is being edited.
function defaultPaidBy() {
  if (state.editingExpenseId) return;
  const sel = $('#expPaidBy');
  const actingAs = $('#actingAs').value;
  if (actingAs && [...sel.options].some((o) => o.value === actingAs)) sel.value = actingAs;
}

$('#actingAs').addEventListener('change', defaultPaidBy);

// Each person keeps one colour for "paid by" in the ledger and on the
// Summary, by when they were added — so up to 8 people never
// share a colour, and disabling or renaming someone doesn't reshuffle them.
// Colours are per theme in style.css (.payer.pc0–pc7).
const PAYER_COLOURS = 8;
function payerClass(userId) {
  const order = [...state.users].sort((a, b) => (a.createdAt || '').localeCompare(b.createdAt || '') || a.id.localeCompare(b.id));
  const i = order.findIndex((u) => u.id === userId);
  return i < 0 ? 'payer' : `payer pc${i % PAYER_COLOURS}`;
}
function payerRowClass(userId) {   // the row only carries the colour (for its left edge), not the text style
  return payerClass(userId).replace(/^payer ?/, '');
}
function payerName(userId, name) {
  return `<span class="${payerClass(userId)}">${escapeHtml(name)}</span>`;
}

// ---------- groups ----------
async function loadGroups() {
  state.groups = await api('/groups');
  renderGroupMemberPicker();
  renderGroups();
  renderNavGroups();
}

// The Groups page (#/groups): a spinner the first time, Retry when the list can't be read.
async function showGroupsPage() {
  showView('groups');
  const list = $('#groupsList');
  if (!state.groups.length) UI.mount(list, UI.h('li', { class: 'state-li' }, UI.loading()));
  try {
    await loadGroups();
  } catch (err) {
    $('#myOverallStrip').hidden = true;
    UI.mount(list, UI.h('li', { class: 'state-li' }, UI.errorBox(err, showGroupsPage)));
    return;
  }
  if ($('#overallAbout').open) loadOverall();
}

// Most recently active first (a new expense, payment or change), then by name.
function sortedGroups() {
  return [...state.groups].sort((a, b) => (b.lastActivity || '').localeCompare(a.lastActivity || '') ||
    a.name.localeCompare(b.name));
}

function renderGroupMemberPicker() {
  const box = $('#groupMemberPicker');
  const selectable = state.users.filter((u) => !u.disabled);
  if (selectable.length === 0) {
    box.innerHTML = '<span class="hint" style="margin:0">No users yet — add people under Settings → People in Home Assistant.</span>';
    return;
  }
  box.innerHTML = selectable
    .map(
      (u) =>
        `<div class="chip ${state.selectedMembersForNewGroup.has(u.id) ? 'selected' : ''}" data-user-chip="${u.id}" role="checkbox" tabindex="0" aria-checked="${state.selectedMembersForNewGroup.has(u.id)}">${escapeHtml(u.name)}</div>`
    )
    .join('');
}

function toggleMemberChip(chip) {
  const id = chip.dataset.userChip;
  if (state.selectedMembersForNewGroup.has(id)) state.selectedMembersForNewGroup.delete(id);
  else state.selectedMembersForNewGroup.add(id);
  renderGroupMemberPicker();
}
$('#groupMemberPicker').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-user-chip]');
  if (chip) toggleMemberChip(chip);
});
$('#groupMemberPicker').addEventListener('keydown', (e) => {
  const chip = e.target.closest('[data-user-chip]');
  if (!chip || (e.key !== 'Enter' && e.key !== ' ')) return;
  e.preventDefault();
  toggleMemberChip(chip);
  const again = $(`#groupMemberPicker [data-user-chip="${CSS.escape(chip.dataset.userChip)}"]`);
  if (again) again.focus();
});

// Where the signed-in person stands in a group: "you owe $5.00", "you're owed $5.00", "settled" — or nothing
// when Home Assistant doesn't link them to a person, or they have no part in the group.
function myStanding(g) {
  if (g.myBalance === null || g.myBalance === undefined) return null;
  if (g.myBalance < -0.004) return { cls: 'negative', text: `you owe ${money(-g.myBalance)}` };
  if (g.myBalance > 0.004) return { cls: 'positive', text: `you're owed ${money(g.myBalance)}` };
  return { cls: 'zero', text: 'settled' };
}

function renderGroups() {
  const list = $('#groupsList');
  list.innerHTML = '';
  renderMyOverall();
  if (state.groups.length === 0) {
    UI.mount(list, UI.h('li', { class: 'state-li' }, UI.empty('No groups yet',
      'Group people together — a trip, a household, a project — then add what each person paid.',
      { label: '+ New group', onclick: openNewGroup, class: 'btn primary' })));
    return;
  }
  sortedGroups().forEach((g) => {
    const li = document.createElement('li');
    const standing = myStanding(g);
    li.className = 'group-row';
    li.innerHTML = `
      <button type="button" class="star-btn tap44 ${g.isDefault ? 'is-default' : ''}" data-toggle-default="${g.id}" data-is-default="${g.isDefault}"
        title="${g.isDefault ? 'Your default group — click to open the Groups list instead' : 'Open this group when Splitpot starts (just for you)'}"
        aria-label="${g.isDefault ? 'Remove as my default group' : 'Set as my default group'}">${g.isDefault ? '★' : '☆'}</button>
      <button type="button" class="group-link" data-open-group="${g.id}">
        <div class="row-main">
          <span class="row-title">${escapeHtml(g.name)}${g.isDefault ? ' <span class="default-badge">Default</span>' : ''}</span>
          <span class="row-sub">${g.memberNames.map(escapeHtml).join(', ')}</span>
          <span class="row-sub">${entryCounts(g)}</span>
        </div>
        ${standing ? `<span class="group-standing amt ${standing.cls}">${escapeHtml(standing.text)}</span>` : ''}
      </button>
    `;
    list.appendChild(li);
  });
}

// The strip at the top of Groups: what you owe and are owed over all your groups (when Home Assistant links you).
function renderMyOverall() {
  const strip = $('#myOverallStrip');
  const mine = state.groups.filter((g) => g.myBalance !== null && g.myBalance !== undefined);
  if (!mine.length) { strip.hidden = true; return; }
  const owe = mine.reduce((a, g) => a + (g.myBalance < 0 ? Math.round(-g.myBalance * 100) : 0), 0) / 100;
  const owed = mine.reduce((a, g) => a + (g.myBalance > 0 ? Math.round(g.myBalance * 100) : 0), 0) / 100;
  const net = Math.round((owed - owe) * 100) / 100;
  const h = UI.h;
  const kpi = (label, value, cls) => h('div', { class: 'kpi' }, h('span', { class: 'kpi-label' }, label), h('span', { class: `kpi-value ${cls || ''}` }, value));
  UI.mount(strip,
    kpi('You owe', money(owe), owe > 0.004 ? 'neg' : ''),
    kpi('You\'re owed', money(owed), owed > 0.004 ? 'pos' : ''),
    kpi('Overall', net > 0.004 ? `+${money(net)}` : net < -0.004 ? `−${money(-net)}` : 'Settled', net > 0.004 ? 'pos' : net < -0.004 ? 'neg' : ''));
  strip.hidden = false;
}

// "Overall balance" (folded under the strip): everyone's net position over every group (GET /dashboard).
async function loadOverall() {
  const box = $('#overallBalanceBox');
  if (!box.firstChild) UI.loading(box);
  try {
    const d = await api('/dashboard');
    renderOverallBalance(d.overallNet);
  } catch (err) {
    UI.mount(box, UI.errorBox(err, loadOverall));
  }
}
$('#overallAbout').addEventListener('toggle', () => { if ($('#overallAbout').open) loadOverall(); });

// "12 expenses · 3 payments" — the same counts in the Groups list and the delete confirmation.
function entryCounts(g) {
  const e = g.expenseCount || 0;
  const p = g.paymentCount || 0;
  const parts = [`${e} expense${e === 1 ? '' : 's'}`];
  if (p) parts.push(`${p} payment${p === 1 ? '' : 's'}`);
  return parts.join(' · ');
}

// Your default group (just yours): Splitpot opens straight into it instead of the Groups list.
async function toggleGroupDefault(id, makeDefault) {
  try {
    await api(`/groups/${id}/default?limit=1`, { method: 'PUT', body: JSON.stringify({ isDefault: makeDefault }) });
    await loadGroups();
  } catch (err) {
    toast(err.message, true);
    return;
  }
  if (state.currentGroupId === id && state.currentGroup) {
    state.currentGroup.isDefault = makeDefault;
    renderGroupDetail();
  }
}

// ⋯ on a group's page: Name and members, Export CSV, Delete group
function closeGroupMenu() {
  const menu = $('#groupMenu');
  if (!menu || menu.hidden) return;
  menu.hidden = true;
  $('#groupMenuBtn').setAttribute('aria-expanded', 'false');
}
$('#groupMenuBtn').addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = $('#groupMenu');
  if (!menu.hidden) { closeGroupMenu(); return; }
  menu.hidden = false;
  $('#groupMenuBtn').setAttribute('aria-expanded', 'true');
  const first = [...menu.querySelectorAll('.pop-item')].find((x) => !x.hidden);
  if (first) first.focus();
});
document.addEventListener('click', (e) => { if (!e.target.closest('.menu-wrap')) closeGroupMenu(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !$('#groupMenu').hidden && !openModals.length) { closeGroupMenu(); $('#groupMenuBtn').focus(); }
});
$('#groupExportBtn').addEventListener('click', () => setTimeout(closeGroupMenu, 0));

$('#groupDeleteBtn').addEventListener('click', async () => {
  closeGroupMenu();
  const g = state.currentGroup;
  if (!g) return;
  const what = g.ledgerTotal ? ` and its ${entryCounts(g).replace(' · ', ' and ')}` : '';
  if (!await confirmAction('Delete group', `Delete the group "${g.name}"${what}? Balances from it disappear too. This can't be undone.`, 'Delete group')) return;
  try {
    await api(`/groups/${g.id}`, { method: 'DELETE' });
    state.currentGroup = null;
    state.currentGroupId = null;
    toast(`Deleted ${g.name}`);
    nav('#/groups', { replace: true });
  } catch (err) {
    toast(err.message, true);
  }
});

// -- the group's name and members (members and admins) --
$('#groupEditBtn').addEventListener('click', () => {
  closeGroupMenu();
  const panel = $('#groupEditPanel');
  panel.hidden = !panel.hidden;
  if (!panel.hidden) {
    renderGroupEdit();
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
});

function renderGroupEdit() {
  const h = UI.h;
  const g = state.currentGroup;
  const err = h('div', { class: 'error', role: 'alert', id: 'groupEditError' });
  const run = async (path, options, done) => {
    err.textContent = '';
    try {
      await changeGroup(path, options);
      if (done) toast(done);
      loadGroups().catch(() => {});
    } catch (ex) {
      err.textContent = ex.message;
    }
  };
  const base = `/groups/${encodeURIComponent(g.id)}`;
  const name = h('input', { type: 'text', id: 'groupRenameInput', maxlength: '80', 'aria-label': 'Group name', value: g.name });
  const rename = h('form', { class: 'field-row group-rename', onsubmit: (ev) => {
    ev.preventDefault();
    run(base, { method: 'PUT', body: JSON.stringify({ name: name.value }) }, 'Renamed');
  } }, name, h('button', { class: 'btn ghost', type: 'submit' }, 'Rename'));
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  const members = h('ul', { class: 'row-list member-edit-list' }, g.members.map((m) => h('li', null,
    h('span', { class: 'row-title' }, m.name, disabledIds.has(m.id) ? ' (disabled)' : ''),
    g.members.length > 1 ? h('button', { class: 'btn danger-text', type: 'button', title: 'Only while their balance in this group is zero',
      onclick: async () => {
        if (!await confirmAction('Take out', `Take ${m.name} out of ${g.name}? Their past entries stay.`, 'Take out')) return;
        run(`${base}/members/${encodeURIComponent(m.id)}`, { method: 'DELETE' }, `${m.name} is no longer in ${g.name}`);
      } }, 'Take out') : null)));
  const others = state.users.filter((u) => !u.disabled && !g.memberIds.includes(u.id));
  const pick = h('select', { id: 'groupAddMember', 'aria-label': 'Add someone' },
    h('option', { value: '' }, others.length ? 'Add someone…' : 'Everyone is in this group'),
    others.map((u) => h('option', { value: u.id }, u.name)));
  const add = h('form', { class: 'field-row' , onsubmit: (ev) => {
    ev.preventDefault();
    if (!pick.value) return;
    run(`${base}/members`, { method: 'POST', body: JSON.stringify({ userId: pick.value }) }, 'Added');
  } }, pick, h('button', { class: 'btn ghost', type: 'submit', disabled: !others.length }, 'Add'));
  UI.mount($('#groupEditPanel'),
    h('h3', null, 'Name'), rename,
    h('h3', null, 'Members'),
    h('p', { class: 'hint' }, 'Someone can be taken out once their balance in this group is zero; their past entries stay.'),
    members, add, err,
    h('div', { class: 'form-actions' }, h('button', { class: 'btn ghost', type: 'button', id: 'groupEditDone',
      onclick: () => { $('#groupEditPanel').hidden = true; } }, 'Done')));
}

$('#groupDefaultBtn').addEventListener('click', () => {
  if (!state.currentGroup) return;
  toggleGroupDefault(state.currentGroup.id, !state.currentGroup.isDefault);
});

$('#groupsList').addEventListener('click', (e) => {
  const starBtn = e.target.closest('[data-toggle-default]');
  if (starBtn) {
    toggleGroupDefault(starBtn.dataset.toggleDefault, starBtn.dataset.isDefault !== 'true');
    return;
  }
  const id = e.target.closest('[data-open-group]')?.dataset.openGroup;
  if (!id) return;
  openGroup(id);
});

// + New group: a sheet with the name and who is in it (you, to start with, when Home Assistant links you)
function openNewGroup() {
  if (!state.selectedMembersForNewGroup.size && state.me && state.users.some((u) => u.id === state.me && !u.disabled)) {
    state.selectedMembersForNewGroup.add(state.me);
  }
  renderGroupMemberPicker();
  $('#groupError').textContent = '';
  openSheet($('#newGroupSheet'), $('#newGroupName'));
}
function closeNewGroup() { closeSheet($('#newGroupSheet')); }
$('#newGroupBtn').addEventListener('click', openNewGroup);
$('#newGroupClose').addEventListener('click', closeNewGroup);

$('#addGroupForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const nameInput = $('#newGroupName');
  const errBox = $('#groupError');
  errBox.textContent = '';
  let created;
  try {
    created = await api('/groups', {
      method: 'POST',
      body: JSON.stringify({ name: nameInput.value.trim(), memberIds: Array.from(state.selectedMembersForNewGroup) }),
    });
  } catch (err) {
    errBox.textContent = err.message;
    return;
  }
  nameInput.value = '';
  state.selectedMembersForNewGroup.clear();
  closeNewGroup();
  await loadGroups().catch(() => {});
  if (created && created.id) openGroup(created.id);
});

// ---------- the sheets: ＋ Add expense, the full expense form, + New group ----------
// Each is a layer for Back; Escape or a tap outside closes the top one.
const SHEETS = ['#newGroupSheet', '#quickAddBackdrop', '#expenseSheet'];
function openSheet(el, focusEl) {
  el.hidden = false;
  if (focusEl) setTimeout(() => focusEl.focus(), 30);
}
function closeSheet(el) {
  if (el.hidden) return;
  el.hidden = true;
  if (el.id === 'expenseSheet') resetExpenseForm();
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || openModals.length) return;   // a dialog on top closes first (by itself)
  const top = [...SHEETS].reverse().find((id) => !$(id).hidden);
  if (top) { e.preventDefault(); closeSheet($(top)); }
});
SHEETS.forEach((id) => {
  const el = $(id);
  let downOnBackdrop = false;
  el.addEventListener('mousedown', (e) => { downOnBackdrop = e.target === el; });
  el.addEventListener('click', (e) => { if (e.target === el && downOnBackdrop) closeSheet(el); });
});

// ---------- single group ----------
// The group's page (#/g/<id>): its name straight away, a spinner while it loads, Retry if it can't.
function showGroup(id) {
  state.groupLoad = loadGroupPage(id);
  return state.groupLoad;
}

async function loadGroupPage(id) {
  const fresh = !(state.currentGroup && state.currentGroup.id === id);
  state.currentGroupId = id;
  state.lastGroupId = id;
  state.editingPaymentId = null;
  $('#groupEditPanel').hidden = true;
  if (fresh) {
    state.currentGroup = null;
    $('#groupTitle').textContent = (state.groups.find((g) => g.id === id) || {}).name || '';
    $('#groupMembersList').replaceChildren();
    $('#groupBody').hidden = true;
    $('.group-head-actions').hidden = true;
    UI.loading($('#groupLoadBox'));
  }
  showView('group');
  try {
    await loadConfig();
    await refreshGroup();
  } catch (err) {
    if (state.currentGroupId !== id) return;
    $('#groupBody').hidden = true;
    $('.group-head-actions').hidden = true;
    UI.mount($('#groupLoadBox'), UI.errorBox(err, () => showGroup(id)));
    return;
  }
  if (state.currentGroupId !== id) return;
  $('#groupLoadBox').replaceChildren();
  $('#groupBody').hidden = false;
  $('.group-head-actions').hidden = false;
  if ($('#expenseSheet').hidden) resetExpenseForm();
}

// How many ledger entries to (re)load: the first page, or — after a change, so the list doesn't jump back —
// as many as are showing now (more than one request when that is over the server's largest page).
function ledgerWanted(keep) {
  const shown = keep && state.currentGroup && state.currentGroup.id === state.currentGroupId ? state.currentGroup.expenses.length : 0;
  return Math.max(LEDGER_PAGE, shown);
}

// The query for a group or a ledger page: `limit`, and everyone's running balances only while
// "Show each person's balance" is ticked (the server works them out only when asked).
function ledgerQuery(limit) {
  return `limit=${Math.min(LEDGER_MAX, limit)}${state.showRunning ? '&balances=1' : ''}`;
}

// Load further pages into `g` until it holds `want` entries (or the whole ledger).
async function topUpLedger(g, want) {
  while (g.nextCursor && g.expenses.length < want) {
    const page = await api(`/groups/${encodeURIComponent(g.id)}/ledger?${ledgerQuery(want - g.expenses.length)}&cursor=${encodeURIComponent(g.nextCursor)}`);
    const seen = new Set(g.expenses.map((e) => e.id));
    g.expenses.push(...page.expenses.filter((e) => !seen.has(e.id)));
    g.nextCursor = page.nextCursor;
    g.ledgerTotal = page.total;
  }
  return g;
}

async function refreshGroup(keep = false) {
  const id = state.currentGroupId;
  const want = ledgerWanted(keep);
  const g = await topUpLedger(await api(`/groups/${encodeURIComponent(id)}?${ledgerQuery(want)}`), want);
  if (state.currentGroupId !== id) return;   // another group was opened meanwhile
  state.currentGroup = g;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  state.splitParticipants = new Set(g.memberIds.filter((x) => !disabledIds.has(x)));
  renderGroupDetail();
}

// A change that answers with the group (payments, members, the name): show that answer — with as many ledger
// entries as were showing — unless another group was opened while it was on its way. Throws the server's message.
async function changeGroup(path, options) {
  const id = state.currentGroupId;
  const want = ledgerWanted(true);
  const g = await topUpLedger(await api(`${path}${path.includes('?') ? '&' : '?'}${ledgerQuery(want)}`, options), want);
  if (state.currentGroupId !== id) return null;
  state.currentGroup = g;
  renderGroupDetail();
  return g;
}

function renderGroupDetail() {
  const g = state.currentGroup;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  const selectableMembers = g.members.filter((m) => !disabledIds.has(m.id));

  $('#groupTitle').textContent = g.name;
  $('#groupMembersList').innerHTML = g.members.map((m) => `<span class="member-pill">${escapeHtml(m.name)}${disabledIds.has(m.id) ? ' (disabled)' : ''}</span>`).join('');
  const defaultBtn = $('#groupDefaultBtn');
  defaultBtn.classList.toggle('is-default', !!g.isDefault);
  defaultBtn.textContent = g.isDefault ? '★ Default' : '☆ Default';
  defaultBtn.setAttribute('aria-pressed', g.isDefault ? 'true' : 'false');
  defaultBtn.title = g.isDefault ? 'Open the Groups list when Splitpot starts, instead of this group (just for you)' : 'Open this group when Splitpot starts, instead of the Groups list (just for you)';
  // Deleting a group is admin-only (enforced server-side too).
  $('#groupDeleteBtn').hidden = !state.isAdmin;
  // The group's name and members: its members and admins (enforced server-side too).
  const canEdit = state.isAdmin || (state.me && g.memberIds.includes(state.me));
  $('#groupEditBtn').hidden = !canEdit;
  if (!canEdit) $('#groupEditPanel').hidden = true;
  if (!$('#groupEditPanel').hidden) renderGroupEdit();
  // Export is for everyone, under ⋯ (relative URL: ingress serves the app under a sub-path)
  const exportBtn = $('#groupExportBtn');
  exportBtn.href = `api/groups/${encodeURIComponent(g.id)}/export.csv`;
  exportBtn.classList.toggle('disabled', !g.ledgerTotal);
  exportBtn.setAttribute('aria-disabled', g.ledgerTotal ? 'false' : 'true');
  exportBtn.title = g.ledgerTotal ? 'Download every expense and payment in this group as a CSV file for a spreadsheet' : 'Nothing to export yet';

  const paidBySel = $('#expPaidBy');
  paidBySel.innerHTML = selectableMembers.map((m) => `<option value="${m.id}">${escapeHtml(m.name)}</option>`).join('');
  const prevPaidBy = paidBySel.value;
  if (state.editingExpenseId && selectableMembers.some((m) => m.id === prevPaidBy)) paidBySel.value = prevPaidBy;
  else defaultPaidBy();

  renderSplitParticipants();
  renderCustomSplitRows();
  renderExpenseList();
  renderBalances();
}

function renderSplitParticipants() {
  const box = $('#splitParticipants');
  const g = state.currentGroup;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  box.innerHTML = g.members
    .filter((m) => !disabledIds.has(m.id))
    .map(
      (m) =>
        `<div class="chip ${state.splitParticipants.has(m.id) ? 'selected' : ''}" data-split-chip="${m.id}">${escapeHtml(m.name)}</div>`
    )
    .join('');
}

$('#splitParticipants').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-split-chip]');
  if (!chip) return;
  const id = chip.dataset.splitChip;
  if (state.splitParticipants.has(id)) state.splitParticipants.delete(id);
  else state.splitParticipants.add(id);
  renderSplitParticipants();
  if (state.splitType === 'custom') renderCustomSplitRows();
});

$$('#addExpenseForm .split-btn[data-split]').forEach((btn) => {
  btn.addEventListener('click', () => {
    setSplitType(btn.dataset.split);
    renderCustomSplitRows();
  });
});

// Custom split: "By amount" (splitType 'custom') or "By percentage"
// (splitType 'percent'; the server rounds to 2 decimals, requires exactly
// 100% and turns them into cents that add up to the amount).
$$('#customModeToggle [data-custom-mode]').forEach((btn) => {
  btn.addEventListener('click', () => {
    setCustomMode(btn.dataset.customMode);
    renderCustomSplitRows();
  });
});

function setCustomMode(mode) {
  state.customMode = mode;
  $$('#customModeToggle [data-custom-mode]').forEach((b) => b.classList.toggle('active', b.dataset.customMode === mode));
}

function renderCustomSplitRows() {
  const box = $('#customSplitRows');
  if (state.splitType !== 'custom') {
    box.innerHTML = '';
    delete box.dataset.mode;
    $('#customModeToggle').hidden = true;
    $('#customSplitSummary').hidden = true;
    return;
  }
  $('#customModeToggle').hidden = false;
  const percentMode = state.customMode === 'percent';
  // Keep what was already typed (same mode) when participants change.
  const typed = {};
  if (box.dataset.mode === state.customMode) {
    $$('[data-custom-input]', box).forEach((i) => { typed[i.dataset.customInput] = i.value; });
  }
  const g = state.currentGroup;
  const members = g.members.filter((m) => state.splitParticipants.has(m.id));
  box.innerHTML = members
    .map((m) => percentMode
      ? `
    <div class="custom-split-row">
      <span class="custom-split-name">${escapeHtml(m.name)}</span>
      <span class="custom-split-share" data-share-for="${escapeHtml(m.id)}"></span>
      <span class="pct-input">
        <input type="number" step="0.01" min="0" max="100" placeholder="0.00" inputmode="decimal"
          data-custom-input="${escapeHtml(m.id)}" aria-label="${escapeHtml(m.name)} percentage" /><span class="pct-suffix">%</span>
      </span>
    </div>`
      : `
    <div class="custom-split-row">
      <span class="custom-split-name">${escapeHtml(m.name)}</span>
      <input type="number" step="0.01" min="0" placeholder="0.00" inputmode="decimal"
        data-custom-input="${escapeHtml(m.id)}" aria-label="${escapeHtml(m.name)} amount" />
    </div>`)
    .join('');
  box.dataset.mode = state.customMode;
  $$('[data-custom-input]', box).forEach((i) => { if (typed[i.dataset.customInput] !== undefined) i.value = typed[i.dataset.customInput]; });
  updateCustomSummary();
}

// Percentages in hundredths of a percent (integers, like the server).
function pctHundredths(value) {
  const n = parseFloat(value);
  return Number.isFinite(n) ? Math.round(n * 100) : 0;
}

function fmtPercent(hundredths) {
  return (hundredths / 100).toFixed(2);
}

// Same largest-remainder rounding as the server (percent_shares): each
// share in whole cents, the leftover cents to the largest remainders (ties
// by order), so the shares add up exactly to the amount. Only exact when
// the percentages total 100%; otherwise just the rounded-down shares.
function percentShareCents(amount, hundredths) {
  const cents = Math.round(amount * 100);
  const base = hundredths.map((h) => (h > 0 ? Math.floor((cents * h) / 10000) : 0));
  const total = hundredths.reduce((a, h) => a + h, 0);
  if (total === 10000 && hundredths.every((h) => h >= 0)) {
    const rem = hundredths.map((h) => (cents * h) % 10000);
    const short = cents - base.reduce((a, b) => a + b, 0);
    hundredths.map((_, i) => i).sort((a, b) => rem[b] - rem[a] || a - b).slice(0, short).forEach((i) => { base[i] += 1; });
  }
  return base;
}

function updateCustomSummary() {
  const summary = $('#customSplitSummary');
  if (state.splitType !== 'custom' || state.customMode !== 'percent') {
    summary.hidden = true;
    return;
  }
  const inputs = $$('#customSplitRows [data-custom-input]');
  summary.hidden = inputs.length === 0;
  const hundredths = inputs.map((i) => pctHundredths(i.value));
  const total = hundredths.reduce((a, h) => a + h, 0);
  const totalEl = $('#customSplitTotal');
  totalEl.textContent = `Total ${fmtPercent(total)}% · remaining ${fmtPercent(10000 - total)}%`;
  totalEl.classList.toggle('off', total !== 10000);
  const amount = parseFloat($('#expAmount').value);
  const shares = amount > 0 ? percentShareCents(amount, hundredths) : null;
  inputs.forEach((input, idx) => {
    const cell = $(`#customSplitRows [data-share-for="${CSS.escape(input.dataset.customInput)}"]`);
    if (!cell) return;
    cell.textContent = shares && input.value.trim() !== '' && hundredths[idx] >= 0 ? money(shares[idx] / 100) : '';
  });
  const empties = inputs.filter((i) => i.value.trim() === '');
  $('#splitRestBtn').disabled = empties.length === 0 || total >= 10000;
}

$('#customSplitRows').addEventListener('input', () => {
  $('#expenseError').textContent = '';   // e.g. a stale "must add up to 100%"
  updateCustomSummary();
});
$('#expAmount').addEventListener('input', updateCustomSummary);

// Fill the empty percentage rows with what's left of 100%, as evenly as
// 2 decimals allow (the last rows get the extra hundredths).
$('#splitRestBtn').addEventListener('click', () => {
  const inputs = $$('#customSplitRows [data-custom-input]');
  const empties = inputs.filter((i) => i.value.trim() === '');
  const used = inputs.filter((i) => i.value.trim() !== '').reduce((a, i) => a + pctHundredths(i.value), 0);
  const left = 10000 - used;
  if (!empties.length || left <= 0) return;
  const each = Math.floor(left / empties.length);
  const extra = left - each * empties.length;
  empties.forEach((input, i) => {
    input.value = fmtPercent(each + (i >= empties.length - extra ? 1 : 0));
  });
  updateCustomSummary();
});

$('#addExpenseForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errBox = $('#expenseError');
  errBox.textContent = '';
  const description = $('#expDescription').value.trim();
  const amount = parseFloat($('#expAmount').value);
  const paidBy = $('#expPaidBy').value;
  const participants = Array.from(state.splitParticipants);

  if (participants.length === 0) {
    errBox.textContent = 'Pick at least one person to split with';
    return;
  }

  const date = $('#expDate').value || localDateInputValue();
  const payload = { description, amount, paidBy, splitType: state.splitType, participants, date };

  if (state.splitType === 'custom') {
    const rows = $$('#customSplitRows [data-custom-input]');
    if (state.customMode === 'percent') {
      const hundredths = rows.map((r) => pctHundredths(r.value));
      if (hundredths.reduce((a, h) => a + h, 0) !== 10000) {
        errBox.textContent = 'Percentages must add up to 100%';
        return;
      }
      payload.splitType = 'percent';
      payload.splits = rows.map((r, i) => ({ userId: r.dataset.customInput, percent: hundredths[i] / 100 }));
    } else {
      payload.splits = rows.map((r) => ({ userId: r.dataset.customInput, amount: parseFloat(r.value || '0') }));
    }
  }

  try {
    if (state.editingExpenseId) {
      await api(`/expenses/${state.editingExpenseId}`, { method: 'PUT', body: JSON.stringify(payload) });
    } else {
      await api(`/groups/${state.currentGroupId}/expenses`, { method: 'POST', body: JSON.stringify(payload) });
    }
    const edited = !!state.editingExpenseId;
    closeSheet($('#expenseSheet'));   // and back to a blank form
    toast(edited ? 'Saved' : 'Expense added');
    await refreshGroup(true);
    loadGroups().catch(() => {});      // where you stand, in the Groups list
  } catch (err) {
    errBox.textContent = err.message;
  }
});

function setSplitType(type) {
  state.splitType = type;
  $$('#addExpenseForm .split-btn[data-split]').forEach((b) => b.classList.toggle('active', b.dataset.split === type));
}

// Back to a blank "Add an expense" form (after saving, closing the sheet,
// or switching groups).
function resetExpenseForm() {
  state.editingExpenseId = null;
  $('#expDescription').value = '';
  $('#expAmount').value = '';
  $('#expDate').value = localDateInputValue();
  $('#expDate').max = localDateInputValue();
  $('#expenseError').textContent = '';
  $('#expenseFormTitle').textContent = 'Add an expense';
  $('#expSubmitBtn').textContent = 'Add expense';
  $('#expDeleteBtn').hidden = true;
  setSplitType('equal');
  setCustomMode('amount');
  if (state.currentGroup) {
    const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
    state.splitParticipants = new Set(state.currentGroup.memberIds.filter((id) => !disabledIds.has(id)));
    renderSplitParticipants();
    renderCustomSplitRows();
    defaultPaidBy();
  }
}

// The full form, in its sheet: a new expense (＋ → Custom split…) or one being changed (a tap on its ledger row).
function openExpenseSheet() {
  openSheet($('#expenseSheet'), $('#expDescription'));
}

// Load an existing expense into the form for editing.
function startEditExpense(id) {
  const e = state.currentGroup.expenses.find((x) => x.id === id);
  if (!e) return;
  state.editingExpenseId = id;
  $('#expDescription').value = e.description;
  $('#expAmount').value = e.amount.toFixed(2);
  const paidBy = $('#expPaidBy');
  if (![...paidBy.options].some((o) => o.value === e.paidBy)) {   // a disabled payer stays possible for this expense
    paidBy.appendChild(UI.h('option', { value: e.paidBy }, `${e.paidByName} (disabled)`));
  }
  paidBy.value = e.paidBy;
  $('#expDate').value = localDateInputValue(parseWhen(e.date));
  state.splitParticipants = new Set(e.splits.map((s) => s.userId));
  const custom = e.splitType === 'custom' || e.splitType === 'percent';
  setSplitType(custom ? 'custom' : 'equal');
  setCustomMode(e.splitType === 'percent' ? 'percent' : 'amount');
  renderSplitParticipants();
  renderCustomSplitRows();
  if (custom) {
    e.splits.forEach((s) => {
      const input = $(`#customSplitRows [data-custom-input="${CSS.escape(s.userId)}"]`);
      if (!input) return;
      input.value = e.splitType === 'percent' && s.percent != null ? Number(s.percent).toFixed(2) : s.amount.toFixed(2);
    });
    updateCustomSummary();
  }
  $('#expenseFormTitle').textContent = 'Edit expense';
  $('#expSubmitBtn').textContent = 'Save changes';
  // Deleting is admin-only (enforced server-side); others don't get a button that would just be refused.
  $('#expDeleteBtn').hidden = !state.isAdmin;
  $('#expenseError').textContent = '';
  openExpenseSheet();
}

$('#expCancelEditBtn').addEventListener('click', () => closeSheet($('#expenseSheet')));
$('#expSheetClose').addEventListener('click', () => closeSheet($('#expenseSheet')));
$('#expDeleteBtn').addEventListener('click', async () => {
  const id = state.editingExpenseId;
  if (id && await deleteEntry(id)) closeSheet($('#expenseSheet'));
});

// Delete an expense or a settle-up payment (admins), after asking. → true when it was deleted.
async function deleteEntry(id) {
  const g = state.currentGroup;
  const e = g && g.expenses.find((x) => x.id === id);
  const label = !e ? 'this entry' : e.splitType === 'payment' ? `this ${money(e.amount)} settle-up payment` : `'${e.description}' (${money(e.amount)})`;
  if (!await confirmAction('Delete', `Delete ${label}? This can't be undone.`, 'Delete')) return false;
  try {
    await api(`/expenses/${encodeURIComponent(id)}`, { method: 'DELETE' });
  } catch (err) {
    toast(err.message, true);
    return false;
  }
  if (state.editingPaymentId === id) state.editingPaymentId = null;
  toast('Deleted');
  refreshGroup(true).catch((err) => toast(err.message, true));
  loadGroups().catch(() => {});
  return true;
}

// One ledger row (UI.h: names and descriptions are set as text).
function expenseRow(e) {
  const h = UI.h;
  const when = parseWhen(e.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  const payer = h('span', { class: payerClass(e.paidBy) }, e.paidByName);
  // The whole row opens its editor (a tap, or Enter on the keyboard); Delete is in there.
  const tap = { tabindex: '0', role: 'button', title: 'Change this entry' };
  if (e.splitType === 'payment') {
    if (state.editingPaymentId === e.id) return paymentEditor(e);
    // A settle-up payment: paidBy paid the one person in splits back.
    const to = e.splits[0] ? e.splits[0].name : 'someone';
    return h('li', { class: `payment-row payer-row tap-row ${payerRowClass(e.paidBy)}`, dataset: { expenseId: e.id }, ...tap },
      h('div', { class: 'row-main' },
        h('span', { class: 'row-title' }, '💸 ', payer, ` paid ${to}`),
        h('span', { class: 'row-sub' }, `${when} · settle-up payment`)),
      h('div', { class: 'row-actions' }, h('span', { class: 'row-amount' }, money(e.amount)),
        h('span', { class: 'row-chev', 'aria-hidden': 'true' }, '›')));
  }
  // Percent splits: "split by percentage · Ann 60% ($30.00) · …"
  const splitDesc = e.splitType === 'percent'
    ? 'by percentage · ' + e.splits.map((s) => `${s.name} ${+Number(s.percent).toFixed(2)}% (${money(s.amount)})`).join(' · ')
    : e.splits.map((s) => `${s.name} ${fmt(s.amount)}`).join(' · ');
  return h('li', { class: `payer-row tap-row ${payerRowClass(e.paidBy)}`, dataset: { expenseId: e.id }, ...tap },
    h('div', { class: 'row-main' },
      h('span', { class: 'row-title' }, e.description),
      h('span', { class: 'row-sub' }, `${when} · paid by `, payer, ` · split ${splitDesc}`)),
    h('div', { class: 'row-actions' },
      h('span', { class: 'row-amount' }, money(e.amount)),
      h('span', { class: 'row-chev', 'aria-hidden': 'true' }, '›')));
}

// Changing a settle-up payment, in its own ledger row: who paid whom, how much and when.
function paymentEditor(e) {
  const h = UI.h;
  const g = state.currentGroup;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  const to = e.splits[0] ? e.splits[0].userId : '';
  const people = g.members.filter((m) => !disabledIds.has(m.id) || m.id === e.paidBy || m.id === to);
  const pick = (label, selected) => h('label', { class: 'field' }, h('span', null, label),
    h('select', { 'aria-label': label, value: selected }, people.map((m) => h('option', { value: m.id }, m.name))));
  const from = pick('Who paid', e.paidBy);
  const toPick = pick('To', to);
  const amount = h('input', { type: 'number', step: '0.01', min: '0.01', inputmode: 'decimal', 'aria-label': 'Amount', value: e.amount.toFixed(2) });
  const date = h('input', { type: 'date', 'aria-label': 'Date', max: localDateInputValue(), value: localDateInputValue(parseWhen(e.date)) });
  const err = h('div', { class: 'error', role: 'alert' });
  const save = async () => {
    err.textContent = '';
    try {
      await changeGroup(`/payments/${encodeURIComponent(e.id)}`, {
        method: 'PUT',
        body: JSON.stringify({ fromUserId: from.querySelector('select').value, toUserId: toPick.querySelector('select').value,
          amount: parseFloat(amount.value), date: date.value || localDateInputValue() }),
      });
      state.editingPaymentId = null;
      if (state.currentGroup) renderExpenseList();
    } catch (ex) {
      err.textContent = ex.message;
    }
  };
  const cancel = () => { state.editingPaymentId = null; renderExpenseList(); };
  return h('li', { class: 'payment-row payment-edit', dataset: { expenseId: e.id } },
    h('div', { class: 'row-main' },
      h('span', { class: 'row-title' }, '💸 Change this settle-up payment'),
      h('div', { class: 'field-row' }, from, toPick,
        h('label', { class: 'field' }, h('span', null, 'Amount'), amount),
        h('label', { class: 'field' }, h('span', null, 'Date'), date)),
      err),
    h('div', { class: 'row-actions' },
      h('button', { class: 'btn primary', type: 'button', onclick: save }, 'Save'),
      h('button', { class: 'btn ghost', type: 'button', onclick: cancel }, 'Cancel'),
      state.isAdmin ? h('button', { class: 'btn danger-text', type: 'button', onclick: () => deleteEntry(e.id) }, 'Delete') : null));
}

// "Show each person's balance": under each entry, everyone's balance in the group just after it (from the server:
// every entry added up oldest first). Off by default; remembered on this device.
const RUNNING_KEY = 'splitpot.ledgerRunningBalances';
state.showRunning = UI.lsGet(RUNNING_KEY) === '1';
$('#showRunningBalances').checked = state.showRunning;
$('#showRunningBalances').addEventListener('change', (ev) => {
  state.showRunning = ev.target.checked;
  UI.lsSet(RUNNING_KEY, state.showRunning ? '1' : '0');
  if (!state.currentGroup) return;
  // The balances come with the entries only when asked for: fetch them (keeping what's loaded) the first time.
  if (state.showRunning && state.currentGroup.expenses.some((e) => !e.balancesAfter)) {
    refreshGroup(true).catch((err) => toast(err.message, true));
  } else {
    renderExpenseList();
  }
});

function runningBalances(e) {
  const h = UI.h;
  const list = (e.balancesAfter || []);
  if (!list.length) return null;
  return h('div', { class: 'row-balances', title: 'Balances just after this entry — + is owed, − owes' },
    list.map((b) => {
      const cls = b.amount > 0.004 ? 'positive' : b.amount < -0.004 ? 'negative' : 'zero';
      const sign = b.amount > 0.004 ? '+' : b.amount < -0.004 ? '−' : '';
      return h('span', { class: `run-bal ${cls}` }, h('span', { class: 'run-name' }, b.name), ` ${sign}${money(Math.abs(b.amount))}`);
    }));
}

// A ledger row plus, when the toggle is on, everyone's balance just after it. Every path that draws ledger rows
// (the first page, Load more) goes through here.
function ledgerRow(e) {
  const row = expenseRow(e);
  if (state.showRunning) {
    const bal = runningBalances(e);
    if (bal) row.querySelector('.row-main').append(bal);
  }
  return row;
}

// The loaded entries under month headers; each header shows what was spent that month in the entries loaded
// (settle-up payments aren't spending).
function ledgerItems(entries) {
  const h = UI.h;
  const monthOf = (e) => { const d = parseWhen(e.date); return `${d.getFullYear()}-${d.getMonth()}`; };
  const spent = {};
  entries.forEach((e) => { if (e.splitType !== 'payment') spent[monthOf(e)] = (spent[monthOf(e)] || 0) + Math.round(e.amount * 100); });
  const out = [];
  let month = null;
  entries.forEach((e) => {
    const k = monthOf(e);
    if (k !== month) {
      month = k;
      out.push(h('li', { class: 'ui-group-head' },
        h('span', null, parseWhen(e.date).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })),
        h('span', { class: 'ui-group-total', title: 'Spent this month (in the entries shown; payments not counted)' },
          spent[k] ? money(spent[k] / 100) : '')));
    }
    out.push(ledgerRow(e));
  });
  return out;
}

function renderExpenseList() {
  const list = $('#expenseList');
  const g = state.currentGroup;
  if (g.expenses.length === 0) {
    UI.mount(list, UI.h('li', { class: 'state-li' }, UI.empty('No expenses yet', 'Add the first one with ＋.',
      { label: 'Add an expense', onclick: () => openQuickAdd(), class: 'btn primary' })));
  } else {
    UI.mount(list, ledgerItems(g.expenses));
  }
  renderLedgerMore();
}

// "Showing 20 of 63" and the Load more button under the ledger.
function renderLedgerMore() {
  const g = state.currentGroup;
  const box = $('#ledgerMore');
  const shown = g.expenses.length;
  const total = g.ledgerTotal == null ? shown : g.ledgerTotal;
  box.hidden = !g.nextCursor && shown >= total;
  $('#ledgerCount').textContent = shown ? `Showing ${shown} of ${total} ${total === 1 ? 'entry' : 'entries'}` : '';
  const btn = $('#ledgerMoreBtn');
  btn.hidden = !g.nextCursor;
  btn.disabled = state.ledgerLoading;
  btn.textContent = state.ledgerLoading ? 'Loading…' : `Load ${Math.min(LEDGER_PAGE, Math.max(0, total - shown)) || LEDGER_PAGE} more`;
}

// Load more: the next page after the last row shown (keyset cursor from the server, so entries added meanwhile
// don't shift or repeat what's already on screen).
async function loadMoreLedger() {
  const g = state.currentGroup;
  if (!g || !g.nextCursor || state.ledgerLoading) return;
  state.ledgerLoading = true;
  renderLedgerMore();
  const groupId = g.id;
  try {
    const page = await api(`/groups/${encodeURIComponent(groupId)}/ledger?${ledgerQuery(LEDGER_PAGE)}&cursor=${encodeURIComponent(g.nextCursor)}`);
    if (state.currentGroup !== g) return;   // the group was reloaded or left meanwhile
    const seen = new Set(g.expenses.map((e) => e.id));
    const fresh = page.expenses.filter((e) => !seen.has(e.id));
    g.expenses.push(...fresh);
    g.nextCursor = page.nextCursor;
    g.ledgerTotal = page.total;
    if (fresh.length) renderExpenseList();   // the month headers' totals take in the new rows too
  } catch (err) {
    toast(err.message, true);
  } finally {
    state.ledgerLoading = false;
    if (state.currentGroup === g) renderLedgerMore();
  }
}

$('#ledgerMoreBtn').addEventListener('click', loadMoreLedger);

// A tap on a ledger row: an expense opens in the full form; a payment opens its editor in place.
function openLedgerEntry(li) {
  const g = state.currentGroup;
  const e = g && g.expenses.find((x) => x.id === li.dataset.expenseId);
  if (!e) return;
  if (e.splitType === 'payment') {
    state.editingPaymentId = e.id;
    renderExpenseList();
    const row = $(`#expenseList li.payment-edit[data-expense-id="${CSS.escape(e.id)}"]`);
    const first = row && row.querySelector('select, input');
    if (first) first.focus();
  } else {
    startEditExpense(e.id);
  }
}
$('#expenseList').addEventListener('click', (e) => {
  const li = e.target.closest('li.tap-row');
  if (li && !e.target.closest('button, a, input, select, label')) openLedgerEntry(li);
});
$('#expenseList').addEventListener('keydown', (e) => {
  if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('li.tap-row')) {
    e.preventDefault();
    openLedgerEntry(e.target);
  }
});

$('#settleBox').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-settle-from]');
  if (btn) recordPayment(btn.dataset.settleFrom, btn.dataset.settleTo, Number(btn.dataset.settleAmount));
});

// Record payment: how much was paid (the full amount filled in; less for a part payment), in a dialog.
function recordPayment(from, to, suggested) {
  const h = UI.h;
  const g = state.currentGroup;
  const nameOf = (id) => (g.members.find((m) => m.id === id) || {}).name || 'Someone';
  const input = h('input', { type: 'number', id: 'payAmount', inputmode: 'decimal', step: '0.01', min: '0.01', required: true,
    value: suggested.toFixed(2) });
  const err = h('div', { class: 'error', role: 'alert' });
  const ok = h('button', { type: 'submit', class: 'btn primary' }, 'Record payment');
  let dialog = null;
  const form = h('form', { class: 'stack-form pay-form', novalidate: true, onsubmit: async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    const amount = Math.round(parseFloat(input.value) * 100) / 100;
    if (!(amount > 0)) { err.textContent = 'Enter an amount greater than 0.'; input.focus(); return; }
    ok.disabled = true;
    try {
      await changeGroup(`/groups/${encodeURIComponent(g.id)}/payments`, {
        method: 'POST',
        body: JSON.stringify({ fromUserId: from, toUserId: to, amount, date: localDateInputValue() }),
      });
    } catch (ex) {
      err.textContent = ex.message;
      ok.disabled = false;
      return;
    }
    dialog.close();
    toast(`Recorded: ${nameOf(from)} paid ${nameOf(to)} ${money(amount)}`);
    loadGroups().catch(() => {});
  } },
    h('p', { class: 'hint' }, `How much did ${nameOf(from)} pay ${nameOf(to)}? The full ${money(suggested)} settles them up; change it for a part payment.`),
    h('div', { class: 'field' }, h('label', { for: 'payAmount' }, 'Amount'), input),
    err,
    h('div', { class: 'form-actions' }, ok, h('button', { type: 'button', class: 'btn ghost', onclick: () => dialog.close() }, 'Cancel')));
  dialog = modal('Record a payment', form);
}

// Balances, then Settle up — your own row first and highlighted (when Home Assistant links you to a person).
function renderBalances() {
  const me = state.me;
  const { net, transfers } = state.currentGroup.balances;
  const meFirst = (list, isMine) => [...list.filter(isMine), ...list.filter((x) => !isMine(x))];
  const netHtml = meFirst(net, (n) => !!me && n.userId === me)
    .map((n) => {
      const cls = n.amount > 0.004 ? 'positive' : n.amount < -0.004 ? 'negative' : 'zero';
      const mine = !!me && n.userId === me;
      const label = mine
        ? (n.amount > 0.004 ? 'you\'re owed' : n.amount < -0.004 ? 'you owe' : 'settled up')
        : (n.amount > 0.004 ? 'is owed' : n.amount < -0.004 ? 'owes' : 'settled up');
      return `<div class="balance-net-row${mine ? ' is-me' : ''}"><span>${escapeHtml(n.name)}${mine ? ' <span class="me-tag">you</span>' : ''}</span><span class="amt ${cls}">${label} ${money(Math.abs(n.amount))}</span></div>`;
    })
    .join('');
  $('#balancesBox').innerHTML = netHtml || '<div class="empty-note">No one in this group yet.</div>';

  const transfersHtml =
    transfers.length === 0
      ? '<div class="all-settled">Everyone is settled up.</div>'
      : meFirst(transfers, (t) => !!me && (t.from === me || t.to === me))
          .map(
            (t) => `
      <div class="settle-row${me && (t.from === me || t.to === me) ? ' is-me' : ''}">
        <span>${escapeHtml(t.fromName)}</span>
        <span class="arrow">→</span>
        <span>${escapeHtml(t.toName)}</span>
        <span class="amt">${money(t.amount)}</span>
        <button type="button" class="btn ghost settle-btn" data-settle-from="${t.from}" data-settle-to="${t.to}" data-settle-amount="${t.amount}" title="Record that ${escapeHtml(t.fromName)} paid ${escapeHtml(t.toName)}">Record payment</button>
      </div>`
          )
          .join('');

  $('#settleBox').innerHTML = transfersHtml;
}

// common/ui.js's escaper; String() first, as before (a missing value shows as "undefined"/"null").
function escapeHtml(str) {
  return UI.escapeHtml(String(str));
}

function renderOverallBalance(net) {
  const box = $('#overallBalanceBox');
  if (net.length === 0) {
    box.innerHTML = '<div class="empty-note">No balances yet — add some expenses in a group.</div>';
    return;
  }
  box.innerHTML = net
    .map((n) => {
      const cls = n.amount > 0.004 ? 'positive' : n.amount < -0.004 ? 'negative' : 'zero';
      const label = n.amount > 0.004 ? 'is owed' : n.amount < -0.004 ? 'owes' : 'settled up';
      return `<div class="balance-net-row"><span>${escapeHtml(n.name)}</span><span class="amt ${cls}">${label} ${money(Math.abs(n.amount))}</span></div>`;
    })
    .join('');
}

// ---------- Summary (spending searched; the overall balance is on Groups) ----------
function isoDay(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

// The preset periods, by this device's calendar (the server reads the days in Home Assistant's time zone)
function periodDays(kind) {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth();
  const monday = (d) => { const x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; };
  switch (kind) {
    case 'last-month': return [new Date(y, m - 1, 1), new Date(y, m, 0)];
    case 'this-week': { const a = monday(now); return [a, now]; }
    case 'last-week': { const a = monday(now); a.setDate(a.getDate() - 7); const b = new Date(a); b.setDate(b.getDate() + 6); return [a, b]; }
    case 'this-year': return [new Date(y, 0, 1), now];
    default: return [new Date(y, m, 1), now];      // this month
  }
}

function fillPeoplePicks(select, anyLabel) {
  const keep = select.value;
  select.innerHTML = `<option value="">${anyLabel}</option>` +
    state.users.filter((u) => !u.disabled).map((u) => `<option value="${escapeHtml(u.id)}">${escapeHtml(u.name)}</option>`).join('');
  select.value = keep;
}

function fillGroupPicks(select) {
  const keep = select.value;
  select.innerHTML = '<option value="">Any group</option>' +
    state.groups.map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`).join('');
  select.value = keep;
}

function setSummaryPeriod(kind) {
  const custom = kind === 'custom';
  if (!custom) {
    const [a, b] = periodDays(kind);
    $('#sumFrom').value = isoDay(a);
    $('#sumTo').value = isoDay(b);
  }
}

// The Spending figures follow the filters by themselves: a period or a pick at once, typed days and amounts a moment
// after the last keystroke (Show still runs it straight away).
const runSummarySoon = UI.debounce(() => runSummary(), 500);
$('#sumPeriod').addEventListener('change', (e) => {
  setSummaryPeriod(e.target.value);
  if (e.target.value === 'custom') $('#sumFrom').focus(); else runSummary();
});
['#sumFrom', '#sumTo'].forEach((id) => {
  $(id).addEventListener('input', () => { $('#sumPeriod').value = 'custom'; });
  $(id).addEventListener('change', runSummarySoon);
});
['#sumMin', '#sumMax'].forEach((id) => $(id).addEventListener('input', () => { markMoreFilters(); runSummarySoon(); }));
['#sumPaidBy', '#sumGroup'].forEach((id) => $(id).addEventListener('change', () => { markMoreFilters(); runSummary(); }));

// Phones: Amount, Paid by and Group fold under "More filters" (the button says how many are set).
function markMoreFilters() {
  const n = ['#sumMin', '#sumMax', '#sumPaidBy', '#sumGroup'].filter((id) => $(id).value.trim() !== '').length;
  $('#sumMoreBtn').textContent = n ? `More filters (${n})` : 'More filters';
}
$('#sumMoreBtn').addEventListener('click', () => {
  const open = !$('#summaryForm').classList.contains('show-more');
  $('#summaryForm').classList.toggle('show-more', open);
  $('#sumMoreBtn').setAttribute('aria-expanded', open ? 'true' : 'false');
});

function amountParams(minEl, maxEl, q) {
  const lo = minEl.value.trim(), hi = maxEl.value.trim();
  if (lo !== '') q.set('min', lo);
  if (hi !== '') q.set('max', hi);
}

async function showSummary() {
  showView('summary');
  fillPeoplePicks($('#sumPaidBy'), 'Anyone');
  fillGroupPicks($('#sumGroup'));
  markMoreFilters();
  if (!$('#sumFrom').value) setSummaryPeriod($('#sumPeriod').value);
  await loadConfig();
  await runSummary();
}

let summarySeq = 0;
async function runSummary() {
  const err = $('#summaryError');
  err.textContent = '';
  const seq = ++summarySeq;   // only the newest answer is shown
  const q = new URLSearchParams({ from: $('#sumFrom').value, to: $('#sumTo').value });
  amountParams($('#sumMin'), $('#sumMax'), q);
  if ($('#sumPaidBy').value) q.set('paidBy', $('#sumPaidBy').value);
  if ($('#sumGroup').value) q.set('groupId', $('#sumGroup').value);
  if (!q.get('from') || !q.get('to')) { err.textContent = 'Pick the days.'; return; }
  const box = $('#summaryResult');
  if (!box.firstChild || box.querySelector('.ui-error')) UI.loading(box);
  box.classList.add('is-loading');
  let r;
  try {
    r = await api('/report?' + q.toString());
  } catch (e) {
    if (seq !== summarySeq) return;
    box.classList.remove('is-loading');
    UI.mount(box, UI.errorBox(e, runSummary));
    return;
  }
  if (seq !== summarySeq) return;
  box.classList.remove('is-loading');
  const day = (iso) => new Date(iso + 'T12:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const head = `<div class="period-summary"><strong>${money(r.total)}</strong> across ${r.count} expense${r.count === 1 ? '' : 's'} · ${day(r.from)} – ${day(r.to)}</div>`;
  if (!r.count) { box.innerHTML = head + '<div class="empty-note">No expenses match.</div>'; return; }
  const max = Math.max(1, ...r.byGroup.map((g) => g.total));
  const groups = r.byGroup.map((g) => `
    <div class="bar-row">
      <span class="bar-label">${escapeHtml(g.name)} <span class="hint">· ${g.count}</span></span>
      <div class="bar-track"><div class="bar-fill" style="width:${(g.total / max) * 100}%"></div></div>
      <span class="bar-amt">${money(g.total)}</span>
    </div>`).join('');
  const people = r.byPerson.map((p) => {
    const diff = Math.round((p.paid - p.share) * 100) / 100;
    const cls = diff > 0.004 ? 'positive' : diff < -0.004 ? 'negative' : 'zero';
    return `<tr>
      <td>${payerName(p.userId, p.name)}</td>
      <td class="num">${money(p.paid)}<span class="hint"> · ${p.paidCount}</span></td>
      <td class="num">${money(p.share)}</td>
      <td class="num amt ${cls}">${diff > 0 ? '+' : diff < 0 ? '−' : ''}${money(Math.abs(diff))}</td>
    </tr>`;
  }).join('');
  // the transactions under a header per day, with that day's total
  const dayKey = (e) => localDateInputValue(parseWhen(e.date));
  const dayTotal = {};
  r.transactions.forEach((e) => { dayTotal[dayKey(e)] = (dayTotal[dayKey(e)] || 0) + Math.round(e.amount * 100); });
  let lastDay = null;
  const rows = r.transactions.map((e) => {
    const k = dayKey(e);
    let head = '';
    if (k !== lastDay) {
      lastDay = k;
      const label = parseWhen(e.date).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
      head = `<li class="ui-group-head"><span>${escapeHtml(label)}</span><span class="ui-group-total">${money(dayTotal[k] / 100)}</span></li>`;
    }
    return `${head}<li class="payer-row ${payerRowClass(e.paidBy)}">
        <div class="row-main">
          <span class="row-title">${escapeHtml(e.description)}</span>
          <span class="row-sub">${escapeHtml(e.groupName)} · paid by ${payerName(e.paidBy, e.paidByName)}</span>
        </div>
        <span class="row-amount">${money(e.amount)}</span>
      </li>`;
  }).join('');
  box.innerHTML = head + `
    <h3 class="sub-head">By group</h3>
    <div class="bar-list">${groups}</div>
    <h3 class="sub-head">By person</h3>
    <div class="table-scroll"><table class="report-table">
      <thead><tr><th>Person</th><th class="num">Paid</th><th class="num">Their share</th><th class="num">Paid − share</th></tr></thead>
      <tbody>${people}</tbody>
    </table></div>
    <p class="hint">Paid: what they paid for these expenses (and how many). Their share: their part of them. Paid − share: ahead (+) or behind (−) on these alone.</p>
    <h3 class="sub-head">Transactions</h3>
    <ul class="row-list">${rows}</ul>
    ${r.truncated ? `<p class="hint">The newest ${r.transactions.length} of ${r.count} are listed; the totals count them all.</p>` : ''}`;
}

$('#summaryForm').addEventListener('submit', (e) => { e.preventDefault(); runSummary(); });
$('#sumClear').addEventListener('click', () => {
  $('#sumPeriod').value = 'this-month';
  setSummaryPeriod('this-month');
  ['#sumMin', '#sumMax', '#sumPaidBy', '#sumGroup'].forEach((id) => { $(id).value = ''; });
  markMoreFilters();
  runSummary();
});

// ---------- Activity (the activity log: the latest 50 when it opens, by day; a search narrows it) ----------
const activity = { query: null, next: null, events: [], seq: 0 };

async function showActivity() {
  showView('activity');
  fillGroupPicks($('#actGroup'));
  searchActivity(false);
  try {
    const actors = await api('/events/actors');
    const sel = $('#actActor'), keep = sel.value;
    sel.innerHTML = '<option value="">Anyone</option>' + actors.map((a) => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join('');
    sel.value = keep;
  } catch (e) { /* the box still works without names */ }
}

function activityQuery() {
  const q = new URLSearchParams();
  const put = (k, el) => { const v = $(el).value.trim(); if (v) q.set(k, v); };
  put('actor', '#actActor'); put('groupId', '#actGroup'); put('from', '#actFrom'); put('to', '#actTo');
  put('min', '#actMin'); put('max', '#actMax'); put('q', '#actWords');
  return q;
}

function eventItem(ev) {
  const when = new Date(ev.createdAt);
  const stamp = when.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit' });
  const time = when.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return `<li>
      <span class="event-dot ${escapeHtml(ev.type)}"></span>
      <div class="event-body">
        <span class="event-message">${escapeHtml(ev.message)}</span>
        <span class="event-time" title="${escapeHtml(stamp)}">${escapeHtml(time)} · ${timeAgo(ev.createdAt)}</span>
      </div>
    </li>`;
}

// "Today", "Yesterday", else "Mon, 12 Oct 2026"
function dayLabel(d) {
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return 'Today';
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function renderActivity() {
  const list = $('#eventLog');
  if (!activity.events.length) {
    list.innerHTML = '<li class="empty-note">Nothing matches.</li>';
    return;
  }
  let day = null;
  list.innerHTML = activity.events.map((ev) => {
    const d = new Date(ev.createdAt);
    let head = '';
    if (d.toDateString() !== day) {
      day = d.toDateString();
      head = `<li class="ui-group-head">${escapeHtml(dayLabel(d))}</li>`;
    }
    return head + eventItem(ev);
  }).join('');
}

async function searchActivity(more = false) {
  const err = $('#activityError');
  err.textContent = '';
  const list = $('#eventLog');
  if (!more) {
    activity.query = activityQuery();
    activity.next = null;
    if (!activity.events.length) UI.mount(list, UI.h('li', { class: 'state-li' }, UI.loading()));
  }
  const seq = ++activity.seq;
  const q = new URLSearchParams(activity.query);
  if (more && activity.next) q.set('before', activity.next);
  let r;
  try {
    r = await api('/events/search?' + q.toString());
  } catch (e) {
    if (seq !== activity.seq) return;
    if (more) { err.textContent = e.message; return; }
    activity.events = [];
    $('#activityMore').hidden = true;
    $('#activityCount').textContent = '';
    UI.mount(list, UI.h('li', { class: 'state-li' }, UI.errorBox(e, () => searchActivity(false))));
    return;
  }
  if (seq !== activity.seq) return;
  activity.events = more ? activity.events.concat(r.events) : r.events;
  activity.next = r.next;
  renderActivity();
  $('#activityMore').hidden = !r.more;
  $('#activityCount').textContent = activity.events.length ? `${activity.events.length} shown${r.more ? ' — there are more' : ''}` : '';
}

$('#activityForm').addEventListener('submit', (e) => { e.preventDefault(); searchActivity(false); });
$('#activityMoreBtn').addEventListener('click', () => searchActivity(true));
$('#actClear').addEventListener('click', () => {
  ['#actActor', '#actGroup', '#actFrom', '#actTo', '#actMin', '#actMax', '#actWords'].forEach((id) => { $(id).value = ''; });
  searchActivity(false);   // back to the latest entries
});

function timeAgo(iso) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  const units = [
    ['year', 31536000],
    ['month', 2592000],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ];
  for (const [name, secs] of units) {
    const n = Math.floor(seconds / secs);
    if (n >= 1) return `${n} ${name}${n === 1 ? '' : 's'} ago`;
  }
  return 'just now';
}

// ---------- ＋ Add expense (every page but the settings pages) ----------
// A quick equal split in any group: on a group's page that group, else the group you used last (else your default,
// else the first), paid by the Acting as person when they're in it, today, shared by every enabled member (untick
// to leave someone out). "Custom split…" opens the full form (in its sheet, on the group's page) with what was typed.
const QA_KEY = 'splitpotQuickGroup';

function qaGroupId() {
  let saved = null;
  try { saved = localStorage.getItem(QA_KEY); } catch (e) { /* private mode */ }
  const ok = (id) => id && state.groups.some((g) => g.id === id);
  if ($('#view-group').classList.contains('active') && ok(state.currentGroupId)) return state.currentGroupId;
  if (ok(saved)) return saved;
  if (ok(state.lastGroupId)) return state.lastGroupId;
  const def = state.groups.find((g) => g.isDefault);
  return def ? def.id : (state.groups[0] || {}).id;
}

function qaMembers(gid) {
  const g = state.groups.find((x) => x.id === gid);
  const disabled = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  return g ? g.memberIds.map((id, i) => ({ id, name: g.memberNames[i] })).filter((m) => !disabled.has(m.id)) : [];
}

function qaFillGroup() {
  const gid = $('#qaGroup').value;
  const members = qaMembers(gid);
  const payer = $('#qaPaidBy').value;
  const acting = $('#actingAs').value;
  $('#qaPaidBy').innerHTML = members.map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name)}</option>`).join('');
  const pick = [payer, acting].find((id) => members.some((m) => m.id === id));
  if (pick) $('#qaPaidBy').value = pick;
  $('#qaPeople').innerHTML = members.map((m) => `<label class="chip selected qa-person"><input type="checkbox" value="${escapeHtml(m.id)}" checked hidden>${escapeHtml(m.name)}</label>`).join('');
}

$('#qaPeople').addEventListener('change', (e) => {
  const cb = e.target.closest('input[type=checkbox]');
  if (cb) cb.closest('label').classList.toggle('selected', cb.checked);
});

async function openQuickAdd() {
  if (!state.groups.length) await loadGroups().catch((err) => toast(err.message, true));
  if (!state.groups.length) { toast('Create a group first.', true); nav('#/groups'); openNewGroup(); return; }
  $('#qaGroup').innerHTML = state.groups.map((g) => `<option value="${escapeHtml(g.id)}">${escapeHtml(g.name)}</option>`).join('');
  $('#qaGroup').value = qaGroupId();
  $('#qaPaidBy').innerHTML = '';
  qaFillGroup();
  $('#qaDescription').value = '';
  $('#qaAmount').value = '';
  $('#qaDate').value = localDateInputValue();
  $('#qaDate').max = localDateInputValue();
  $('#qaError').textContent = '';
  openSheet($('#quickAddBackdrop'), $('#qaDescription'));
}

function closeQuickAdd(refocus = true) {
  closeSheet($('#quickAddBackdrop'));
  if (refocus && !$('#quickAddBtn').hidden) $('#quickAddBtn').focus();
}

$('#quickAddBtn').addEventListener('click', openQuickAdd);
$('#qaClose').addEventListener('click', closeQuickAdd);
$('#qaGroup').addEventListener('change', qaFillGroup);

$('#quickAddForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const gid = $('#qaGroup').value;
  const people = [...$$('#qaPeople input:checked')].map((c) => c.value);
  const err = $('#qaError');
  err.textContent = '';
  if (!people.length) { err.textContent = 'Tick at least one person to share it.'; return; }
  const btn = $('#quickAddForm button[type=submit]');
  btn.disabled = true;
  try {
    await api(`/groups/${encodeURIComponent(gid)}/expenses`, {
      method: 'POST',
      body: JSON.stringify({
        description: $('#qaDescription').value.trim(), amount: Number($('#qaAmount').value), paidBy: $('#qaPaidBy').value,
        date: $('#qaDate').value, splitType: 'equal', participants: people,
      }),
    });
  } catch (ex) {
    err.textContent = ex.message;
    btn.disabled = false;
    return;
  }
  btn.disabled = false;
  try { localStorage.setItem(QA_KEY, gid); } catch (ex) { /* private mode */ }
  const g = state.groups.find((x) => x.id === gid);
  closeQuickAdd();
  toast(`Added to ${g ? g.name : 'the group'}.`);
  if ($('#view-summary').classList.contains('active')) runSummary();
  loadGroups().catch(() => {});
  if ($('#view-group').classList.contains('active') && state.currentGroupId === gid) {
    refreshGroup(true).catch((ex) => toast(ex.message, true));
  }
});

$('#qaMore').addEventListener('click', async () => {
  const gid = $('#qaGroup').value;
  const typed = { d: $('#qaDescription').value, a: $('#qaAmount').value, p: $('#qaPaidBy').value, t: $('#qaDate').value };
  closeQuickAdd(false);
  const here = $('#view-group').classList.contains('active') && state.currentGroupId === gid && state.currentGroup;
  if (!here) {
    openGroup(gid);
    await state.groupLoad;
    if (!(state.currentGroup && state.currentGroup.id === gid)) return;   // it couldn't load (the page says why)
  }
  resetExpenseForm();
  $('#expDescription').value = typed.d;
  $('#expAmount').value = typed.a;
  if ([...$('#expPaidBy').options].some((o) => o.value === typed.p)) $('#expPaidBy').value = typed.p;
  if (typed.t) $('#expDate').value = typed.t;
  const custom = $('#addExpenseForm .split-btn[data-split="custom"]');
  if (custom) custom.click();               // the Custom split, as if tapped
  openExpenseSheet();
});

// ---------- Home Assistant integration ----------
// Home Assistant tells us who's signed in; the server matches that to a
// Person (by HA user id, see /whoami's userId). They become "Acting as" —
// and so "Paid by" — before the first group or the Summary renders.
function linkHomeAssistantUser(who) {
  if (!who) return;
  $('#sidebarUserName').textContent = who.haDisplayName || who.haUsername || 'unknown';
  let id = who.userId;
  if (!id && who.haDisplayName) {   // no link yet — fall back to the name
    const byName = state.users.filter((u) => !u.disabled && u.name.toLowerCase() === who.haDisplayName.toLowerCase());
    if (byName.length === 1) id = byName[0].id;
  }
  if (!id || !state.users.some((u) => u.id === id && !u.disabled)) return;
  renderActingAs();
  $('#actingAs').value = id;
}

// ---------- first run: "No admin yet" ----------
// While admin_users is empty nobody can open App settings, so every page
// says how to become the first administrator (the server never promotes
// anyone by itself). Built with textContent: the name comes from a header.
function renderNoAdminBanner(who) {
  HouseholdWhoami.fillNoAdminBanner($('#noAdminBanner'), !!(who && who.noAdmin),
    who && (who.haUsername || who.haUserId), { onOpen: openWhoami, linkId: 'noAdminWhoami' });
}

// ---------- admin: restore database from backup ----------
(function () {
  const btn = $('#restoreDbBtn');
  const fileInput = $('#restoreDbFile');
  const status = $('#restoreDbStatus');
  if (!btn || !fileInput) return;
  btn.addEventListener('click', async () => {
    const file = fileInput.files[0];
    if (!file) {
      status.textContent = 'Choose a .db file first.';
      return;
    }
    if (!await confirmAction('Import database',
      'This will REPLACE all current data with the contents of this file and cannot be undone. Continue?', 'Replace everything')) {
      return;
    }
    btn.disabled = true;
    status.textContent = 'Importing…';
    try {
      const formData = new FormData();
      formData.append('file', file);
      // headers: {} overrides api()'s default JSON content-type so the
      // browser can set its own multipart boundary for the file upload.
      await api('/admin/import-db', { method: 'POST', body: formData, headers: {} });
      status.textContent = 'Import complete. Reloading…';
      setTimeout(() => location.reload(), 1200);
    } catch (err) {
      status.textContent = err.message || 'Import failed.';
      btn.disabled = false;
    }
  });
})();

// ---------- init ----------
(async function init() {
  await loadConfig();
  let who = null;
  try {
    who = await api('/whoami');   // before the ledger renders its Delete buttons
    state.isAdmin = !!who.isAdmin;
    state.sensorSyncEnabled = !!who.sensorSyncEnabled;
    state.haConnected = !!who.haConnected;
    state.me = who.userId || null;
  } catch (e) { /* not fatal — treated as a non-admin */ }
  $('#adminTabBtn').hidden = !state.isAdmin;   // the server enforces it too
  $('#actingAs').hidden = !state.isAdmin;      // logging on someone else's behalf: admins
  renderNoAdminBanner(who);
  try { await loadUsers(); } catch (e) { /* the pickers stay empty; each page says what failed */ }
  linkHomeAssistantUser(who);   // before any group opens, so "Paid by" starts as you
  try { await loadGroups(); } catch (e) { /* the Groups page shows Retry */ }
  let started = false;
  let linkedGroup = null;
  // A notification opens "/<page>/group/<id>" (common/static/deeplink.js): that group now, and any tapped later
  HouseholdDeepLink.start(state.page, (r) => groupFromHash('#' + r), (gid) => {
    if (!started) linkedGroup = gid;
    else openGroup(gid);
  });
  started = true;
  if (linkedGroup && groupFromHash(location.hash) !== linkedGroup) nav(`#/g/${linkedGroup}`, { replace: true });
  else route();   // the address (#/g/<id>, #/summary, #/admin/users, …), or the start page
})().finally(initBackNav);   // after the first page renders (even if a load failed)

// ---------- Back gesture (backnav.js) ----------
// In the HA app the back gesture walks the iframe's history. backnav.js keeps
// one guard entry while something is open or we're away from the start page:
// Back closes what's open first (dialogs, the sheets, ⋯ menus, a payment or the
// group's name and members being changed), then retraces the pages you opened,
// then the page's parent (a group → Groups), then the start page; only Back on
// the start page leaves for Home Assistant.
// Start page = what the app opens to without a deep link: the default group
// if one is set, otherwise the Groups list.
function startGroup() {
  return state.groups.find((g) => g.isDefault) || null;
}

function homeHash() {
  const g = startGroup();
  return g ? `#/g/${g.id}` : '#/groups';
}

function atStartPage() {
  const g = startGroup();
  if (g) return $('#view-group').classList.contains('active') && state.currentGroupId === g.id;
  return $('#view-groups').classList.contains('active');
}

function goStartPage() {
  nav(homeHash(), { replace: true });
}

function initBackNav() {
  if (!window.BackNav) return;
  const onGroup = () => $('#view-group').classList.contains('active');
  window.BackNav.init({
    atHome: atStartPage,
    goHome: goStartPage,
    isTab: isTabHash,
    parentOf: parentOfHash,
    // What's open, bottom-most first: the group's name-and-members panel and a payment being changed in its
    // ledger row, the ⋯ menus, the sheets (＋ Add expense, the full expense form, + New group), and on top
    // the dialogs (confirmations, Record payment — common/ui.js, through modal() / confirmAction()).
    openLayers: () => {
      const out = [];
      if (onGroup() && !$('#groupEditPanel').hidden) out.push($('#groupEditPanel'));
      if (onGroup() && state.editingPaymentId) out.push('payment');
      if ($('#tabs').classList.contains('more-open')) out.push($('#navMore'));
      if (!$('#groupMenu').hidden) out.push($('#groupMenu'));
      SHEETS.forEach((id) => { if (!$(id).hidden) out.push($(id)); });
      return out.concat(openModals);
    },
    closeLayer: (layer) => {
      if (typeof layer === 'function') layer();                       // a dialog's close
      else if (layer === 'payment') { state.editingPaymentId = null; renderExpenseList(); }
      else if (layer === $('#groupEditPanel')) layer.hidden = true;
      else if (layer === $('#navMore')) closeMore();
      else if (layer === $('#groupMenu')) closeGroupMenu();
      else closeSheet(layer);
    },
  });
}
