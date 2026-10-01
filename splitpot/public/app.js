const state = {
  users: [],
  groups: [],
  currentGroupId: null,
  currentGroup: null,
  selectedMembersForNewGroup: new Set(),
  splitType: 'equal',
  customMode: 'amount',   // Custom split: 'amount' | 'percent'
  splitParticipants: new Set(),
  period: 'week',
  periodOffset: 0,
  isAdmin: false,
  sensorSyncEnabled: false,
  haConnected: false,
  currency: 'USD',
  adminTab: 'settings',
  adminUsers: [],
  editingExpenseId: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function api(path, options = {}) {
  const res = await fetch('api' + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || body.error || 'Something went wrong');
  }
  if (res.status === 204) return null;
  return res.json();
}

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
// settings shows up on the next Dashboard/group load, without reloading.
async function loadConfig() {
  try {
    state.currency = (await api('/config')).currency || state.currency;
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

// ---------- theme switcher ----------
// The bootstrap <script> in <head> already applied any saved theme before
// this file even loaded (beating first paint); this just wires the
// <select> to reflect + change it.
(function () {
  const select = document.getElementById('theme-select');
  if (!select) return;
  const current = document.documentElement.getAttribute('data-theme') || 'paper';
  select.value = current;
  select.addEventListener('change', () => {
    const theme = select.value;
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem('theme', theme); } catch (e) { /* ignore */ }
  });
})();

// ---------- sidebar collapse ----------
// Same before-first-paint trick as the theme: the bootstrap <script> in
// <head> already applied any saved collapsed state via
// [data-sidebar="collapsed"] on <html>; this just wires the toggle button
// and keeps its arrow direction/label in sync.
(function () {
  const btn = document.getElementById('sidebarCollapseBtn');
  if (!btn) return;

  function isCollapsed() {
    return document.documentElement.getAttribute('data-sidebar') === 'collapsed';
  }

  function syncButton() {
    const collapsed = isCollapsed();
    btn.textContent = collapsed ? '›' : '‹';
    btn.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    btn.setAttribute('aria-label', btn.title);
  }

  btn.addEventListener('click', () => {
    const collapsed = !isCollapsed();
    if (collapsed) {
      document.documentElement.setAttribute('data-sidebar', 'collapsed');
    } else {
      document.documentElement.removeAttribute('data-sidebar');
    }
    try { localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0'); } catch (e) { /* ignore */ }
    syncButton();
  });

  syncButton();
})();

// ---------- navigation ----------
// Only the Admin area has URLs (#/admin/settings, #/admin/users,
// #/admin/storage); every other view is shown in place. The short forms
// #/people, #/users, #/storage and #/settings open the matching Admin tab.
const ADMIN_TABS = ['settings', 'users', 'storage'];
const SHORT_ADMIN_ROUTES = { '#/people': 'users', '#/users': 'users', '#/storage': 'storage', '#/settings': 'settings' };

function adminTabFromHash(hash) {
  if (Object.prototype.hasOwnProperty.call(SHORT_ADMIN_ROUTES, hash)) return SHORT_ADMIN_ROUTES[hash];
  const m = /^#\/admin(?:\/([\w-]*))?\/?$/.exec(hash);
  if (!m) return null;
  return ADMIN_TABS.includes(m[1]) ? m[1] : 'settings';
}

function showView(name) {
  $$('.view').forEach((v) => v.classList.remove('active'));
  $$('.tab').forEach((t) => t.classList.remove('active'));
  if (name === 'dashboard' || name === 'groups' || name === 'admin') {
    $(`.tab[data-view="${name}"]`).classList.add('active');
  }
  $(`#view-${name}`).classList.add('active');
  // Leaving the Admin area: drop its #/admin/... from the address. In place
  // (replaceState): in-app navigation adds no history entries — Back is
  // handled by backnav.js (see "Back gesture" below).
  if (name !== 'admin' && location.hash) history.replaceState(null, '', location.pathname + location.search);
}

$('#tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  if (btn.dataset.view === 'admin') {
    openAdmin(state.adminTab);
    return;
  }
  showView(btn.dataset.view);
  if (btn.dataset.view === 'groups') loadGroups();
  if (btn.dataset.view === 'dashboard') loadDashboard();
});

// Hash routes: a typed/pasted #/admin/... address or BackNav.go(). Back
// doesn't come through here — backnav.js handles it (see "Back gesture").
window.addEventListener('hashchange', () => {
  const tab = adminTabFromHash(location.hash);
  if (tab) {
    openAdmin(tab);
  } else if (!location.hash && $('#view-admin').classList.contains('active')) {
    showView('dashboard');   // Back from the Admin area
    loadDashboard();
  }
});

$('#backToGroups').addEventListener('click', () => {
  showView('groups');
  loadGroups();
});

// ---------- "How the app sees you" ----------
// Read-only diagnostic: exactly which user name / id Home Assistant sent, and
// whether they match the admin_users list. Reached from the user chip in the
// sidebar and the 👤 button in the top bar (phones have no sidebar footer).
function openWhoami() {
  showView('whoami');
  loadWhoami();
}

async function loadWhoami() {
  const body = $('#whoamiBody');
  let w;
  try {
    w = await api('/whoami');
  } catch (e) {
    body.innerHTML = `<p class="hint">${escapeHtml(e.message)}</p>`;
    return;
  }

  const name = w.nameSent ? w.haUsername : null;
  const yn = (v) => `<strong>${v ? 'Yes' : 'No'}</strong>`;
  const copyBtn = (text) => text
    ? `<button type="button" class="btn ghost kv-copy" data-copy="${escapeHtml(text)}" title="Copy" aria-label="Copy">⧉</button>`
    : '';
  const row = (label, value, copy) =>
    `<div class="kv-row"><div class="kv-label">${label}</div><div class="kv-value">${value}${copyBtn(copy)}</div></div>`;

  let advice;
  if (w.isAdmin) {
    advice = 'You are an administrator.';
  } else if (w.displayNameOnly) {
    advice = `Your <strong>display name</strong> is in the <code>admin_users</code> list, but display names aren't accepted there (anyone could share or take a name). Replace it with <strong>${escapeHtml(name || w.haUserId || '')}</strong> (or <strong>${escapeHtml(w.haUserId || '')}</strong>), save, and <strong>restart</strong> the app.`;
  } else if (w.adminEntries === 0) {
    advice = `The <code>admin_users</code> list is <strong>empty</strong> in the running app, so nobody is an administrator yet. Add <strong>${escapeHtml(name || w.haUserId || '')}</strong> to <code>admin_users</code> in the app's Configuration tab, save, and <strong>restart</strong> the app (Settings → Apps → Splitpot → Information tab → Restart) — the list is only read when the app starts.`;
  } else {
    const n = w.adminEntries;
    advice = `Neither your user name nor user id above matches any of the ${n} name${n === 1 ? '' : 's'} in the <code>admin_users</code> list. Add <strong>${escapeHtml(name || w.haUserId || '')}</strong> (or <strong>${escapeHtml(w.haUserId || '')}</strong>) exactly as shown, save, and <strong>restart</strong> the app — the list is only read when the app starts. Upper and lower case don't matter.`;
  }

  body.innerHTML =
    `<div class="kv">` +
      row('User name (sent by Home Assistant)', escapeHtml(name || 'not sent'), name) +
      row('User id (sent by Home Assistant)', `<code>${escapeHtml(w.haUserId || '')}</code>`, w.haUserId) +
      row('Display name (not used for matching)', escapeHtml(w.haDisplayName || 'not sent')) +
      row('Administrator in this app', yn(w.isAdmin)) +
      row("Names in the app's admin_users", String(w.adminEntries)) +
    `</div>` +
    `<p class="hint">${advice}</p>`;

  body.querySelectorAll('[data-copy]').forEach((btn) => btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      btn.textContent = '✓';
      setTimeout(() => { btn.textContent = '⧉'; }, 1200);
    } catch (e) { /* clipboard unavailable (insecure context) — the value is on screen to copy by hand */ }
  }));
}

$('#sidebarUser').addEventListener('click', openWhoami);
$('#whoamiBtn').addEventListener('click', openWhoami);

// ---------- users (picker list, for everyone) ----------
async function loadUsers() {
  state.users = await api('/users');
  renderActingAs();
}

// ---------- Admin area ----------
function openAdmin(tab = 'settings') {
  if (!ADMIN_TABS.includes(tab)) tab = 'settings';
  const target = `#/admin/${tab}`;
  // Always in place: in-app navigation adds no history entries (backnav.js).
  if (location.hash !== target) history.replaceState(null, '', target);
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
  if (tab === 'settings') loadSettings();
  if (tab === 'users') loadAdminUsers();
}

$('.admin-tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-admin-tab]');
  if (btn) openAdmin(btn.dataset.adminTab);
});
$('#adminDeniedWhoami').addEventListener('click', openWhoami);

// -- App settings --
function fillSettings(s) {
  $('#setCurrency').value = s.values.currency;
  $('#setSyncEnabled').checked = !!s.values.ha_sync_enabled;
  $('#setSyncInterval').value = s.values.sync_interval_minutes;
  $('#syncNoHaNote').hidden = state.haConnected;
  updateSyncFields();
  updateCurrencyPreview();
}

// The interval only matters while sync is on: grey it out (reflects the
// checkbox as ticked, before saving). A disabled input is still sent.
function updateSyncFields() {
  const on = $('#setSyncEnabled').checked;
  $('#setSyncInterval').disabled = !on;
  $('#syncIntervalField').classList.toggle('is-disabled', !on);
  $('#syncOffNote').hidden = on;
}

async function loadSettings() {
  $('#settingsError').textContent = '';
  $('#settingsStatus').textContent = '';
  try {
    fillSettings(await api('/admin/settings'));
  } catch (err) {
    $('#settingsError').textContent = err.message;
  }
}

function updateCurrencyPreview() {
  const code = $('#setCurrency').value.trim().toUpperCase();
  $('#setCurrencyPreview').textContent = /^[A-Z]{3}$/.test(code) ? money(1234.5, code) : '—';
}

$('#setCurrency').addEventListener('input', () => {
  $('#settingsStatus').textContent = '';
  updateCurrencyPreview();
});
$('#setSyncInterval').addEventListener('input', () => { $('#settingsStatus').textContent = ''; });
$('#setSyncEnabled').addEventListener('change', () => {
  $('#settingsStatus').textContent = '';
  updateSyncFields();
});

$('#settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const errBox = $('#settingsError');
  const status = $('#settingsStatus');
  const btn = $('#settingsSaveBtn');
  errBox.textContent = '';
  status.textContent = '';
  const raw = $('#setSyncInterval').value.trim();
  // The server validates (1–60, whole minutes, ISO 4217) and says what's wrong.
  const payload = {
    ha_sync_enabled: $('#setSyncEnabled').checked,
    currency: $('#setCurrency').value.trim().toUpperCase(),
    sync_interval_minutes: raw === '' ? null : Number(raw),
  };
  btn.disabled = true;
  try {
    const s = await api('/admin/settings', { method: 'PUT', body: JSON.stringify(payload) });
    state.currency = s.values.currency;   // every amount rendered from now on uses it
    state.sensorSyncEnabled = state.haConnected && !!s.values.ha_sync_enabled;
    fillSettings(s);
    status.textContent = 'Saved.';
  } catch (err) {
    errBox.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

// -- Users --
async function loadAdminUsers() {
  const errBox = $('#usersError');
  errBox.textContent = '';
  try {
    state.adminUsers = await api('/admin/users');
  } catch (err) {
    errBox.textContent = err.message;
    return;
  }
  renderAdminUsers();
}

function renderAdminUsers() {
  const list = $('#usersList');
  list.innerHTML = '';
  if (state.adminUsers.length === 0) {
    list.innerHTML = '<li class="empty-note">No Home Assistant users found yet — add people under Settings → People in Home Assistant.</li>';
    return;
  }
  state.adminUsers.forEach((u) => {
    const li = document.createElement('li');
    const groups = `In ${u.groupCount} group${u.groupCount === 1 ? '' : 's'}`;
    const sub = u.disabled ? `${groups} · Disabled — hidden from selection` : groups;
    li.innerHTML = `
      <div class="row-main">
        <span class="row-title">${escapeHtml(u.name)}</span>
        <span class="row-sub">${escapeHtml(sub)}${u.haEntityId ? ` · <code>${escapeHtml(u.haEntityId)}</code>` : ''}</span>
      </div>
      <button class="btn ghost" data-toggle-disabled="${escapeHtml(u.id)}" data-currently-disabled="${u.disabled}">${u.disabled ? 'Enable' : 'Disable'}</button>
    `;
    list.appendChild(li);
  });
}

$('#usersList').addEventListener('click', async (e) => {
  const id = e.target.dataset.toggleDisabled;
  if (!id) return;
  const currentlyDisabled = e.target.dataset.currentlyDisabled === 'true';
  const errBox = $('#usersError');
  errBox.textContent = '';
  try {
    await api(`/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ disabled: !currentlyDisabled }) });
    await loadAdminUsers();
    await loadUsers();   // the pickers
  } catch (err) {
    errBox.textContent = err.message;
  }
});

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
// Dashboard, by when they were added — so up to 8 people never
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
        `<div class="chip ${state.selectedMembersForNewGroup.has(u.id) ? 'selected' : ''}" data-user-chip="${u.id}">${escapeHtml(u.name)}</div>`
    )
    .join('');
}

$('#groupMemberPicker').addEventListener('click', (e) => {
  const chip = e.target.closest('[data-user-chip]');
  if (!chip) return;
  const id = chip.dataset.userChip;
  if (state.selectedMembersForNewGroup.has(id)) state.selectedMembersForNewGroup.delete(id);
  else state.selectedMembersForNewGroup.add(id);
  renderGroupMemberPicker();
});

function renderGroups() {
  const list = $('#groupsList');
  list.innerHTML = '';
  if (state.groups.length === 0) {
    list.innerHTML = '<li class="empty-note">No groups yet — create one above.</li>';
    return;
  }
  state.groups.forEach((g) => {
    const li = document.createElement('li');
    li.innerHTML = `
      <button type="button" class="star-btn ${g.isDefault ? 'is-default' : ''}" data-toggle-default="${g.id}" data-is-default="${g.isDefault}"
        title="${g.isDefault ? 'Remove as default group' : 'Open this group when the app starts'}"
        aria-label="${g.isDefault ? 'Remove as default group' : 'Set as default group'}">${g.isDefault ? '★' : '☆'}</button>
      <button class="group-link" data-open-group="${g.id}">
        <div class="row-main">
          <span class="row-title">${escapeHtml(g.name)}${g.isDefault ? ' <span class="default-badge">Default</span>' : ''}</span>
          <span class="row-sub">${g.memberNames.map(escapeHtml).join(', ')}</span>
        </div>
      </button>
      <span class="row-sub">${g.expenseCount} expense${g.expenseCount === 1 ? '' : 's'}</span>
    `;
    list.appendChild(li);
  });
}

async function toggleGroupDefault(id, makeDefault) {
  await api(`/groups/${id}/default`, { method: 'PUT', body: JSON.stringify({ isDefault: makeDefault }) });
  await loadGroups();
  if (state.currentGroupId === id && state.currentGroup) {
    state.currentGroup.isDefault = makeDefault;
    renderGroupDetail();
  }
}

$('#groupDeleteBtn').addEventListener('click', async () => {
  const g = state.currentGroup;
  if (!g) return;
  const n = g.expenses.length;
  const what = n ? ` and its ${n} expense${n === 1 ? '' : 's'}/payment${n === 1 ? '' : 's'}` : '';
  if (!confirm(`Delete the group "${g.name}"${what}? Balances from it disappear too. This can't be undone.`)) return;
  try {
    await api(`/groups/${g.id}`, { method: 'DELETE' });
    state.currentGroup = null;
    state.currentGroupId = null;
    showView('groups');
    await loadGroups();
  } catch (err) {
    alert(err.message);
  }
});

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

$('#addGroupForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const nameInput = $('#newGroupName');
  const errBox = $('#groupError');
  errBox.textContent = '';
  try {
    await api('/groups', {
      method: 'POST',
      body: JSON.stringify({ name: nameInput.value.trim(), memberIds: Array.from(state.selectedMembersForNewGroup) }),
    });
    nameInput.value = '';
    state.selectedMembersForNewGroup.clear();
    await loadGroups();
  } catch (err) {
    errBox.textContent = err.message;
  }
});

// ---------- single group ----------
async function openGroup(id) {
  state.currentGroupId = id;
  state.currentGroup = null;
  showView('group');
  await loadConfig();
  await refreshGroup();
  resetExpenseForm();
}

async function refreshGroup() {
  const g = await api(`/groups/${state.currentGroupId}`);
  state.currentGroup = g;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  state.splitParticipants = new Set(g.memberIds.filter((id) => !disabledIds.has(id)));
  renderGroupDetail();
}

function renderGroupDetail() {
  const g = state.currentGroup;
  const disabledIds = new Set(state.users.filter((u) => u.disabled).map((u) => u.id));
  const selectableMembers = g.members.filter((m) => !disabledIds.has(m.id));

  $('#groupTitle').textContent = g.name;
  $('#groupMembersList').innerHTML = g.members.map((m) => `<span class="member-pill">${escapeHtml(m.name)}${disabledIds.has(m.id) ? ' (disabled)' : ''}</span>`).join('');
  const defaultBtn = $('#groupDefaultBtn');
  defaultBtn.classList.toggle('is-default', !!g.isDefault);
  defaultBtn.innerHTML = g.isDefault ? '★ Default group' : '☆ Set as default';
  defaultBtn.title = g.isDefault ? 'Open the Dashboard when the app starts, instead of this group' : 'Open this group when the app starts, instead of the Dashboard';
  // Deleting a group is admin-only (enforced server-side too).
  $('#groupDeleteBtn').hidden = !state.isAdmin;

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
    resetExpenseForm();
    await refreshGroup();
  } catch (err) {
    errBox.textContent = err.message;
  }
});

function setSplitType(type) {
  state.splitType = type;
  $$('#addExpenseForm .split-btn[data-split]').forEach((b) => b.classList.toggle('active', b.dataset.split === type));
}

// Back to a blank "Add an expense" form (after saving, cancelling an edit,
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
  $('#expCancelEditBtn').hidden = true;
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

// Load an existing expense into the form for editing.
function startEditExpense(id) {
  const e = state.currentGroup.expenses.find((x) => x.id === id);
  if (!e) return;
  state.editingExpenseId = id;
  $('#expDescription').value = e.description;
  $('#expAmount').value = e.amount.toFixed(2);
  $('#expPaidBy').value = e.paidBy;
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
  $('#expCancelEditBtn').hidden = false;
  $('#expenseError').textContent = '';
  $('#addExpenseForm').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('#expCancelEditBtn').addEventListener('click', resetExpenseForm);

function renderExpenseList() {
  const list = $('#expenseList');
  const g = state.currentGroup;
  list.innerHTML = '';
  if (g.expenses.length === 0) {
    list.innerHTML = '<li class="empty-note">No expenses logged yet.</li>';
    return;
  }
  g.expenses.forEach((e) => {
    const li = document.createElement('li');
    const when = parseWhen(e.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    // Deleting is admin-only (enforced server-side); others don't get a
    // button that would just be refused.
    const deleteBtn = state.isAdmin ? `<button class="btn danger-text" data-remove-expense="${e.id}">Delete</button>` : '';
    if (e.splitType === 'payment') {
      // A settle-up payment: paidBy paid the one person in splits back.
      const to = e.splits[0] ? e.splits[0].name : 'someone';
      li.className = `payment-row payer-row ${payerRowClass(e.paidBy)}`;
      li.innerHTML = `
      <div class="row-main">
        <span class="row-title">💸 ${payerName(e.paidBy, e.paidByName)} paid ${escapeHtml(to)}</span>
        <span class="row-sub">${when} · settle-up payment</span>
      </div>
      <div style="display:flex; align-items:center; gap:14px;">
        <span class="row-amount">${money(e.amount)}</span>
        ${deleteBtn}
      </div>
    `;
    } else {
      // Percent splits: "split by percentage · Ann 60% ($30.00) · …"
      const splitDesc = e.splitType === 'percent'
        ? 'by percentage · ' + e.splits.map((s) => `${escapeHtml(s.name)} ${+Number(s.percent).toFixed(2)}% (${money(s.amount)})`).join(' · ')
        : e.splits.map((s) => `${escapeHtml(s.name)} ${fmt(s.amount)}`).join(' · ');
      li.className = `payer-row ${payerRowClass(e.paidBy)}`;
      li.innerHTML = `
      <div class="row-main">
        <span class="row-title">${escapeHtml(e.description)}</span>
        <span class="row-sub">${when} · paid by ${payerName(e.paidBy, e.paidByName)} · split ${splitDesc}</span>
      </div>
      <div style="display:flex; align-items:center; gap:14px;">
        <span class="row-amount">${money(e.amount)}</span>
        <button class="btn ghost" data-edit-expense="${e.id}">Edit</button>
        ${deleteBtn}
      </div>
    `;
    }
    list.appendChild(li);
  });
}

$('#expenseList').addEventListener('click', async (e) => {
  const editId = e.target.dataset.editExpense;
  if (editId) {
    startEditExpense(editId);
    return;
  }
  const id = e.target.dataset.removeExpense;
  if (!id) return;
  const expense = state.currentGroup.expenses.find((x) => x.id === id);
  const label = expense ? `'${expense.description}' (${money(expense.amount)})` : 'this expense';
  if (!confirm(`Delete ${label}? This can't be undone.`)) return;
  try {
    await api(`/expenses/${id}`, { method: 'DELETE' });
    if (state.editingExpenseId === id) resetExpenseForm();
    await refreshGroup();
  } catch (err) {
    alert(err.message);
  }
});

$('#balancesBox').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-settle-from]');
  if (!btn) return;
  const g = state.currentGroup;
  const nameOf = (id) => (g.members.find((m) => m.id === id) || {}).name || 'Someone';
  const from = btn.dataset.settleFrom;
  const to = btn.dataset.settleTo;
  const suggested = Number(btn.dataset.settleAmount);
  const answer = prompt(`How much did ${nameOf(from)} pay ${nameOf(to)}?`, suggested.toFixed(2));
  if (answer === null) return;
  const amount = parseFloat(answer);
  if (!(amount > 0)) {
    alert('Enter an amount greater than 0.');
    return;
  }
  try {
    state.currentGroup = await api(`/groups/${g.id}/payments`, {
      method: 'POST',
      body: JSON.stringify({ fromUserId: from, toUserId: to, amount, date: localDateInputValue() }),
    });
    renderGroupDetail();
  } catch (err) {
    alert(err.message);
  }
});

function renderBalances() {
  const box = $('#balancesBox');
  const { net, transfers } = state.currentGroup.balances;
  const netHtml = net
    .map((n) => {
      const cls = n.amount > 0.004 ? 'positive' : n.amount < -0.004 ? 'negative' : 'zero';
      const label = n.amount > 0.004 ? 'is owed' : n.amount < -0.004 ? 'owes' : 'settled up';
      return `<div class="balance-net-row"><span>${escapeHtml(n.name)}</span><span class="amt ${cls}">${label} ${money(Math.abs(n.amount))}</span></div>`;
    })
    .join('');

  const transfersHtml =
    transfers.length === 0
      ? '<div class="all-settled">Everyone is settled up.</div>'
      : transfers
          .map(
            (t) => `
      <div class="settle-row">
        <span>${escapeHtml(t.fromName)}</span>
        <span class="arrow">→</span>
        <span>${escapeHtml(t.toName)}</span>
        <span class="amt">${money(t.amount)}</span>
        <button type="button" class="btn ghost settle-btn" data-settle-from="${t.from}" data-settle-to="${t.to}" data-settle-amount="${t.amount}" title="Record that ${escapeHtml(t.fromName)} paid ${escapeHtml(t.toName)}">Record payment</button>
      </div>`
          )
          .join('');

  box.innerHTML = `${netHtml}<div style="height:6px"></div>${transfersHtml}`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------- dashboard ----------
async function loadDashboard() {
  await loadConfig();
  const data = await api('/dashboard');
  $('#statGroups').textContent = data.groupsCount;
  $('#statPeople').textContent = data.peopleCount;
  $('#statWeek').textContent = money(data.week.total);
  $('#statMonth').textContent = money(data.month.total);
  renderOverallBalance(data.overallNet);
  await loadPeriod();
  await loadEvents();
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

$('#periodToggle').addEventListener('click', (e) => {
  const btn = e.target.closest('.split-btn');
  if (!btn) return;
  state.period = btn.dataset.period;
  state.periodOffset = 0;
  $$('#periodToggle .split-btn').forEach((b) => b.classList.toggle('active', b === btn));
  loadPeriod();
});

$('#periodPrev').addEventListener('click', () => {
  state.periodOffset -= 1;
  loadPeriod();
});

$('#periodNext').addEventListener('click', () => {
  if (state.periodOffset >= 0) return;
  state.periodOffset += 1;
  loadPeriod();
});

async function loadPeriod() {
  const data = await api(`/transactions?period=${state.period}&offset=${state.periodOffset}`);
  $('#periodLabel').textContent = data.label;
  $('#periodNext').disabled = state.periodOffset >= 0;
  $('#periodSummary').innerHTML = `<strong>${money(data.total)}</strong> across ${data.count} transaction${data.count === 1 ? '' : 's'}`;
  renderPeriodBreakdown(data.breakdown);
  renderPeriodTransactions(data.expenses);
}

function renderPeriodBreakdown(breakdown) {
  const box = $('#periodBreakdown');
  const max = Math.max(1, ...breakdown.map((b) => b.amount));
  box.innerHTML = breakdown
    .map(
      (b) => `
    <div class="bar-row">
      <span class="bar-label">${escapeHtml(b.label)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(b.amount / max) * 100}%"></div></div>
      <span class="bar-amt">${money(b.amount)}</span>
    </div>`
    )
    .join('');
}

function renderPeriodTransactions(expenses) {
  const list = $('#periodTransactions');
  if (expenses.length === 0) {
    list.innerHTML = '<li class="empty-note">No transactions in this period.</li>';
    return;
  }
  list.innerHTML = expenses
    .map((e) => {
      const when = parseWhen(e.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
      return `
      <li class="payer-row ${payerRowClass(e.paidBy)}">
        <div class="row-main">
          <span class="row-title">${escapeHtml(e.description)}</span>
          <span class="row-sub">${when} · ${escapeHtml(e.groupName)} · paid by ${payerName(e.paidBy, e.paidByName)}</span>
        </div>
        <span class="row-amount">${money(e.amount)}</span>
      </li>`;
    })
    .join('');
}

async function loadEvents() {
  const events = await api('/events?limit=50');
  const list = $('#eventLog');
  if (events.length === 0) {
    list.innerHTML = '<li class="empty-note">Nothing has happened yet.</li>';
    return;
  }
  list.innerHTML = events
    .map(
      (ev) => `
    <li>
      <span class="event-dot ${ev.type}"></span>
      <div class="event-body">
        <span class="event-message">${escapeHtml(ev.message)}</span>
        <span class="event-time">${timeAgo(ev.createdAt)}</span>
      </div>
    </li>`
    )
    .join('');
}

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

// ---------- Home Assistant integration ----------
// Home Assistant tells us who's signed in; the server matches that to a
// Person (by HA user id, see /whoami's userId). They become "Acting as" —
// and so "Paid by" — before the first group or Dashboard renders.
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
  const banner = $('#noAdminBanner');
  const show = !!(who && who.noAdminYet);
  banner.hidden = !show;
  if (show) $('#noAdminName').textContent = who.haUsername || who.haUserId || 'your user name';
}
$('#noAdminWhoami').addEventListener('click', openWhoami);

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
    if (!confirm('This will REPLACE all current data with the contents of this file and cannot be undone. Continue?')) {
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
  } catch (e) { /* not fatal — treated as a non-admin */ }
  $('#adminTabBtn').hidden = !state.isAdmin;   // the server enforces it too
  renderNoAdminBanner(who);
  await loadUsers();
  linkHomeAssistantUser(who);   // before any group opens, so "Paid by" starts as you
  await loadGroups();
  const defaultGroup = state.groups.find((g) => g.isDefault);
  const adminTab = adminTabFromHash(location.hash);   // deep link to an Admin tab
  if (adminTab) {
    openAdmin(adminTab);
  } else if (defaultGroup) {
    await openGroup(defaultGroup.id);
  } else {
    await loadDashboard();
  }
})().finally(initBackNav);   // after the first page renders (even if a load failed)

// ---------- Back gesture (backnav.js) ----------
// In the HA app the back gesture walks the iframe's history. backnav.js keeps
// one guard entry while something is open or we're away from the start page:
// Back cancels an expense edit first, then returns to the start page, and
// only Back on the start page leaves for Home Assistant.
// Start page = what the app opens to without a deep link: the default group
// if one is set, otherwise the Dashboard.
function startGroup() {
  return state.groups.find((g) => g.isDefault) || null;
}

function atStartPage() {
  const g = startGroup();
  if (g) return $('#view-group').classList.contains('active') && state.currentGroupId === g.id;
  return $('#view-dashboard').classList.contains('active');
}

function goStartPage() {
  const g = startGroup();
  if (g) {
    openGroup(g.id);
  } else {
    showView('dashboard');
    loadDashboard();
  }
}

function initBackNav() {
  if (!window.BackNav) return;
  window.BackNav.init({
    atHome: atStartPage,
    goHome: goStartPage,
    // The only "layer": editing an expense (the add-expense form in edit mode).
    // Delete/Record-payment use the browser's own confirm()/prompt().
    openLayers: () => (
      state.editingExpenseId && $('#view-group').classList.contains('active') ? [$('#addExpenseForm')] : []
    ),
    closeLayer: () => resetExpenseForm(),
  });
}
