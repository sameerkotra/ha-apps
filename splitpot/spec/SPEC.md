# Splitpot — rebuild spec

Companion files: `schema.sql` (exact DDL), `data-model.mermaid` (ER diagram). Shared conventions (repository root): `HA_ADDON_PATTERNS.md` §4, `WHOAMI_PAGE_SPEC.md`, `SHARING_AN_ADDON.md`. User guide: `DOCS.md`.

## 1. Purpose & scope

Splitwise-style household expense splitter, shipped only as a Home Assistant app behind Ingress.
- Users (the table is `users`; the picker API is `/api/users`) come only from HA `person.*` entities that are linked to an HA user. The app cannot create or delete users.
- There is no login of its own. Identity comes from the Ingress headers; admin rights come from the `admin_users` option. While that list is empty every page shows a "No admin yet" banner; nobody is promoted automatically.
- An admin-only **Admin** area holds App settings (sensor sync on/off, sensor sync interval, currency, phone notifications — stored in SQLite, applied live), Users (enable/disable, phones from Home Assistant, extra notify services, Send a test) and Storage (backup/restore).
- Optional phone notifications through Home Assistant when a charge is added (and when a settle-up payment is recorded) to the people in it, except whoever added it (§7.1); each person can opt out under **My settings**.
- The group ledger loads a page at a time (newest 20, then **Load more**), with keyset paging (§6 "Ledger paging").
- Groups → expenses (equal split, or custom by amount or by percentage) → per-group net balances and suggested settle-up transfers; settle-up payments can be recorded.
- A dashboard shows overall balances, week/month spending and the activity log. Each person's balance is pushed to HA as a sensor.
- Deliberately absent: local login; user create/delete endpoints; removing members or renaming groups; any HA sensor except `sensor.splitpot_balance_<slug>`; a stored or cached `balances` table.

## 2. Stack & file layout

Python 3.12 (`python:3.12-slim`, which includes tzdata), FastAPI + uvicorn, raw `sqlite3` (no ORM). Frontend is plain HTML/CSS/vanilla JS with no build step, served by the same app.

```
splitpot/
├── config.yaml  Dockerfile  .dockerignore  README.md (app store page)  DOCS.md (Documentation tab: the user guide)
├── CHANGELOG.md           # shown in Home Assistant's update dialog
├── icon.png (128×128)  logo.png (250×100)
├── requirements.txt       # fastapi==0.141.1, uvicorn[standard]==0.53.0, python-multipart==0.0.32
├── requirements-dev.txt   # -r requirements.txt + httpx2==2.13.1 (TestClient)
├── translations/en.yaml   # option names/descriptions
├── app/__init__.py
├── app/main.py            # entire backend
├── app/config.py          # what the shared HA modules read at call time: SUPERVISOR_TOKEN, SUPERVISOR_CORE_API, INGRESS_PANEL
├── app/db.py              # get_conn() for the shared modules (→ main.get_conn, looked up at call time)
├── app/common/            # shared Python (copies): whoami, ha_client, ha_time, housekeeping, auth_core, db_core,
│                          #   settings_core, web_security, backup_core, sensor_publisher, csv_export,
│                          #   ha_notify, ha_people, people_admin
├── app/static/index.html, app.js, style.css
├── app/static/common/     # shared browser files (copies): theme-boot.js, themes.css, ui.js, settings.js,
│                          #   settings.css, people.js, backnav.js, whoami.js
├── tests/_env.py, test_app.py, test_percent_split.py, test_admin_settings.py, test_security_headers.py, test_packaging.py,
│   test_notifications.py, test_ledger_paging.py
├── tests/common_tests/    # shared test helpers and shared-module tests (copies)
└── spec/SPEC.md, schema.sql, data-model.mermaid
```
- `app/common/`, `app/static/common/` and `tests/common_tests/` are copies of the repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see `common/README.md`). Never edit a copy: edit `common/` and re-sync; `tests/common_tests/test_shared_copies.py` fails if a copy was changed. Python imports them as `from .common import …`.
- `.dockerignore`: `__pycache__ *.pyc .venv data *.db *.db-* *.log .git tests spec *.md !README.md icon.png logo.png translations requirements-dev.txt` (the image needs only `app/` and the requirements).
- Dockerfile: `ARG BUILD_ARCH`/`ARG BUILD_VERSION`; `WORKDIR /app`; pip install the requirements; copy `app/`; `EXPOSE 8099`; `LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"`; `CMD sh -c "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8099}"`. Use the build arg for the version label: a hard-coded version drifts from config.yaml.
- `python-multipart` is required by the restore upload (`UploadFile`). Without it the app starts fine but the upload fails when it is first called.

## 3. Manifest & options

`config.yaml`: `name: "Splitpot"`, `version` (the release; also every `?v=` in `index.html`), `slug: splitpot`, `url: https://github.com/sameerkotra/ha-apps`, a one- or two-sentence `description`, arch `amd64 aarch64 armv7 armhf i386`, `startup: application`, `boot: auto`, `ingress: true`, `ingress_port: 8099`, `panel_icon: mdi:cash-multiple`, `panel_title: Splitpot`, `panel_admin: false`, `homeassistant_api: true`, and the explicit denials `hassio_api/auth_api/docker_api/full_access: false` with `apparmor: true`. `environment: {DATA_DIR: /data, PORT: "8099"}`. No `ports:` (Ingress only), no `map:` (the Supervisor always provides `/data`).

| Option | Schema | Default | Use |
|---|---|---|---|
| `admin_users` | list of str | `[]` | HA user ids or login names, case-insensitive. Deny by default. Stays here: you must be an admin to open App settings |

`admin_users` is the only option (`options:`, `schema:` and `translations/en.yaml` contain nothing else): read once at import from `/data/options.json` (missing or malformed file → `[]`); changes need a restart. Every other key in `options.json` is ignored; in particular `ha_sync_enabled`, `sync_interval_minutes` and `currency` exist only as App settings (below) and are never read from it or imported.

**App settings** (table `app_settings`, section "App settings" in `app/main.py`):

| Key | Validation (`AppSettings`, `extra="forbid"`) | Default | Used by | Restart? |
|---|---|---|---|---|
| `ha_sync_enabled` | strict bool (`"false"`, `0`, `null` are a 422) | `true` | `sensor_sync_enabled()` = token present AND this; checked by every push, `remove_balance_sensors`, `periodic_sync` and `PUT /admin/settings` | no — off removes the sensors, on pushes at once |
| `sync_interval_minutes` | strict int, 1–60 (a string, float or bool is a 422) | `5` | `periodic_sync` via `seconds_until_next_sync` | no — next cycle |
| `currency` | string, stripped + uppercased, `[A-Z]{3}` and in `ISO_4217_CODES` (active codes plus ANG/SLL/ZWL; no metals/fund/test codes) | `"USD"` | backend `money()`, `balance_sensor_states`, `GET /api/config` → frontend `money()` | no |
| `notify_charges` | strict bool | `false` | `notify_new_charge` / `notify_payment` (§7.1), `GET /api/me/prefs` | no |
| `notify_payments` | strict bool, `enabled_if` `notify_charges` | `true` | `notify_payment` (needs `notify_charges` too) | no |

- Notifications are **off by default on new and existing installs** (no row = the default), like Arcade's record notifications: an admin opts the household in. `notify_payments` defaults on so that switching the main switch on covers settle-ups too. Group `notify` ("Notifications", help: edits and deletions are never notified).

- The settings are declared once in `main.py` — `SETTINGS` (`settings_core.Setting(...)`: label, help, group, range, `enabled_if`, page options) and `SETTINGS_GROUPS` (Money, Home Assistant, Notifications) — on the shared `settings_core.Registry` (`SETTINGS_REGISTRY`, `app/common/settings_core.py`), which validates, stores and caches them and builds the GET/PUT payload; the `settings_changed` activity-log line is its `on_write` hook.
- `settings_all()` / `get_setting(key)` read through an in-memory cache, invalidated by `update_settings` and every `init_db()` (so also after a restore). A generation counter stops a read that raced a write from caching stale values. A stored value that fails validation falls back to the default with a warning.
- `update_settings(partial, user)`: unknown keys → `SettingsError` (422); validates the merged result; writes only the keys whose value changed (`updated_by` = the admin's display/login name); logs one `settings_changed` event; nothing is written on error.
- Rows whose key isn't a known setting are ignored when reading (so a stray row is harmless).

## 4. Security, identity & admin

**Ingress middleware** (`require_ha_ingress_auth`, runs on every request):
1. `request.client.host` must be in `auth_core.INGRESS_HOSTS` (`{172.30.32.2, 127.0.0.1, ::1}`; `auth_core.refuse_outsiders`). Anything else gets **403** `"Forbidden — access only via Home Assistant"`. This blocks other containers from reaching the API directly and skipping Ingress auth.
2. For paths starting with `/api/`, the `X-Remote-User-Id` header is required, otherwise **401**. Static files are exempt so the page shell can load.

**Security headers** (`web_security.SecurityHeaders(CSP, pragma=True).install(app)`, policy `CSP` in `main.py`): every response gets `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' https://fonts.gstatic.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` (Google Fonts allowed: `fonts.googleapis.com` styles, `fonts.gstatic.com` fonts) and `X-Content-Type-Options: nosniff`. No inline scripts or `on…=` handlers in the page.

**Identity headers:** `X-Remote-User-Id`, `X-Remote-User-Name` (login name), `X-Remote-User-Display-Name`, `X-Ingress-Path`.
- The actor written to `events.actor` is `display name → login name → "Someone"`. It is independent of `paid_by`, since one person can log another person's expense.

**Admin** (`is_admin`, on `app/common/auth_core.py`): a request is admin when the lowercased `X-Remote-User-Id` or `X-Remote-User-Name` is in `ADMIN_NAMES` (`auth_core.admin_names`), the set of lowercased, stripped `admin_users` entries.
- Display names are **never** matched: they aren't unique and anyone can pick one.
- `no_admin_yet()` = `ADMIN_NAMES` is empty (a fresh install; `auth_core.no_admin`). `/api/whoami` reports it as `noAdmin` to everyone; nobody becomes admin until a name is listed and the app restarted.
- If the display name alone matches, the check returns false and logs a one-time warning per user id naming the id or login name to list instead. `/api/whoami` reports this as `displayNameOnly`.
- `require_admin` is a route dependency (`auth_core.require_admin_flag`) that returns 403 `"Only designated admins can do this. See the admin_users option in the app's Configuration tab."`. It guards: `GET /api/admin/settings`, `PUT /api/admin/settings`, `GET /api/admin/users`, `GET /api/admin/notify-services`, `POST /api/admin/users/{id}/notify`, `DELETE /api/admin/users/{id}/notify/{service}`, `POST /api/admin/users/{id}/notify/test`, `PATCH /api/users/{id}`, `DELETE /api/expenses/{id}`, `DELETE /api/groups/{id}`, `GET /api/admin/backup-db`, `POST /api/admin/import-db`.
- Everyone else can: list users for the pickers (`GET /api/users`), create groups, add members, add and edit expenses, record payments, see balances/dashboard, set the default group, and read/change their own My settings (`/api/me/prefs`).
- Hiding admin-only buttons in the UI is cosmetic only; the server enforces every rule.

**Other:**
- `escapeHtml` (common/ui.js's escaper, applied after `String()`) escapes `&<>"'`, because values are also used inside attributes. Ingress pages run on HA's origin, so an XSS would run with the viewer's HA session.
- Request validation errors return **422** as one readable sentence `{"detail": "field: msg; …"}` (the `body` part of the location is dropped). The handler never echoes the input: an echoed `Infinity` can't be JSON-encoded and would turn the 422 into a 500.

## 5. Data model

Eight tables; see `schema.sql`. Every connection (`db_core.connect`, `app/common/db_core.py`) sets `PRAGMA foreign_keys = ON`, uses `timeout=10` and `row_factory=Row`. `get_conn = db_core.closing(_connect)`: the connection is closed at the end of the block and never committed automatically (the code inside commits). The database runs in WAL mode.

A process-wide `threading.Lock` (`_lock`) serialises every write. Reads take no lock. Ids are `uuid4` strings; `now_iso()` is a UTC ISO timestamp.

| Table | Notes |
|---|---|
| `users` | Only the HA sync inserts or renames users (keyed by `ha_entity_id`, which has a partial unique index). `ha_user_id` is the Person's `attributes.user_id`, lower-cased, refreshed on every sync; it identifies the signed-in person. No row is ever deleted, so historical expenses always resolve. `disabled=1` hides the person from every picker; a re-sync never resets it. `receive_notifications` (default 1) is the person's own opt-out (My settings) |
| `user_notify` | Extra notify services per person (`"notify.<name>"`), PK `(user_id, service)`, `created_at`, `created_by` (the admin's login name or id). Keyed by Splitpot's **own** `users.id` (FK, cascade) — not the HA user id, which is what the shared table means in the apps whose users.id *is* the HA id; see §7.1 |
| `groups` | `is_default`: at most one group, enforced by the write and backstopped by the partial unique index `idx_groups_one_default` |
| `group_members` | PK `(group_id,user_id)`; `position` keeps add order |
| `expenses` | `amount` is the total. `split_type` is `equal`, `custom` (amounts), `percent` or `payment`. `date` is either a full UTC timestamp or a bare `YYYY-MM-DD` meaning that calendar day in HA's time zone |
| `expense_splits` | One row per person per expense, in entry order (read `ORDER BY rowid`); shares sum to the amount. `percent` (nullable) is set only for `percent` expenses |
| `events` | Append-only. `group_id` is a soft reference (no FK). `message` is pre-rendered text |
| `app_settings` | `key` PK, `value` JSON, `updated_at`, `updated_by` (the admin's display/login name). A missing row means the default |

- **Cascades:** deleting a group removes its members, expenses and splits; deleting an expense removes its splits. `events` rows are never deleted. Old events keep a dangling `group_id`, so `groupName` becomes null.
- **Migrations:** `init_db()` runs at import and after every restore. It does `CREATE TABLE/INDEX IF NOT EXISTS` (including `app_settings`, so an older backup gets it on restore), then adds the columns of `main.MIGRATIONS` that are missing (`db_core.add_missing_columns`: `users.ha_entity_id`, `events.actor`, `users.disabled`, `users.ha_user_id`, `expense_splits.percent`, `groups.is_default`, `users.receive_notifications`, in that order; `user_notify` and the index `idx_expenses_group_date (group_id, date, id)` come from `CREATE … IF NOT EXISTS`). It then creates `idx_users_ha_entity` and `idx_groups_one_default` and sets WAL mode.
- **Payment:** stored as an expense with `paid_by=from`, one split `{to: amount}`, `split_type='payment'` and description `"<From> paid <To>"`. The existing balance maths settles it automatically. Payments are **excluded from spending**: `/api/transactions`, dashboard week/month totals, `expensesCount`, and each group's `expenseCount`.
- **Event types** (message formats in parentheses):
  - `group_created` ("A created group 'G' with N members")
  - `member_added`
  - `group_deleted` (no `group_id`)
  - `group_default_set` / `group_default_cleared`
  - `expense_added` ("A added 'D' ($X) to G[, paid by P]"; the suffix appears only when the payer isn't the actor)
  - `expense_edited` ("… → 'D2' ($Y)" when the description or amount changed)
  - `payment_recorded`
  - `expense_deleted` (also used for payments)
  - `user_disabled` / `user_enabled` (only when the value changed; no `group_id`)
  - `settings_changed` ("A changed the app settings: currency USD → EUR, sync interval 5 → 10 min"; no `group_id`)
- Amounts in messages use backend `money()` with the currency setting at the time the message is written: a symbol map for 21 codes (e.g. `€1,234.50`), otherwise `"THB 2.00"`. Old messages keep their text.

**Balance maths**:
- **Balances are never stored.** They are recomputed from expenses and splits on every request.
- **Per-group (`compute_balances`):**
  - Every member starts at 0. For each expense, `net[payer] += amount` and `net[split.user] -= share`. Each net is then rounded to 2 dp.
  - Debtors are people with net < −0.001 and creditors those with net > 0.001, each sorted by size, largest first.
  - Greedy matching: take the head debtor and head creditor and create a transfer of `min(debt, credit)`, rounded to 2 dp. Subtract it from both, and advance past whichever side drops below 0.005. Repeat until either list runs out.
- **Overall (`compute_overall_net`):** each person's per-group nets summed across all groups, sorted descending. The greedy transfers are **not** run on this total: people in unrelated groups would be told to pay each other.
- **Equal split:** participants are deduplicated in order; `share = round(amount/n, 2)`; the last person gets `round(amount − share·(n−1), 2)`, so shares sum exactly to the amount.
- **Custom split (`custom`, by amount):** amounts must sum to the total within ±0.02. Zero shares are dropped, and at least one share must be > 0.
- **Percent split (`percent`, `percent_shares`):** each `SplitItem.percent` is rounded to 2 dp and the rounded values must total exactly 100.00 (checked in integer hundredths; 400 "Percentages must add up to 100%"). Working in integers — `cents = round(amount·100)`, `h = round(percent·100)` — each share is `cents·h // 10000`; the `cents − Σshares` leftover cents (always fewer than the number of rows) go one each to the largest remainders `cents·h % 10000`, ties by list order. So the shares add up exactly to the amount and none is negative. 0% rows are dropped (at least one must be > 0); a row with a positive percentage can still get $0.00 on a tiny amount and is kept. Stored with each row's `percent`. Examples: 100 at 33.33/33.33/33.34 → 33.33/33.33/33.34; 10.00 → 3.33/3.33/3.34; 0.01 at 50/50 → 0.01/0.00; 1000.01 at 12.5%×8 → 125.01 + 7×125.00.
- Balances, totals, sensors and the activity log only use `expense_splits.amount`, so `percent` expenses behave exactly like `custom` ones.
- Money amounts are always rounded to 2 dp before storing.

**Expense validation** (`ExpenseCreate`, shared by add and edit):
- `description`: at most 200 characters, and not blank after trimming (400).
- `amount`: `Field(gt=0, le=1_000_000, allow_inf_nan=False)`.
- `SplitItem`: `userId` plus `amount` (`ge=0, le=1e6`, finite; required for `custom`, else 400) or `percent` (`ge=0, le=100`, finite; required for `percent`, else 400). NaN/Infinity/negative/>100 are 422.
- `splitType`: `equal`, `custom` or `percent`, default `equal`.
- The payer must be a group member. Everyone in the split must be a member, and custom/percent splits must not repeat a person (400).
- `date`, via `resolve_expense_date`:
  - Empty, or today's date in the local zone, is stored as `now_iso()`.
  - Any other date is stored as bare `YYYY-MM-DD`.
  - 400 for an unparseable date, a date more than one day in the future, or a year before 2000.
- `PaymentCreate`: `fromUserId ≠ toUserId`, both must be members, amount validated like expenses, optional date.

## 6. API

Routes are on `APIRouter(prefix="/api")`, registered before `app/static/` is mounted at `/` (`StaticFiles(html=True)`, the folder next to `main.py`). Request and response bodies use camelCase. "Group" below means the full `serialize_group` response:
- group fields: `{id, name, createdAt, isDefault, memberIds, members:[{id,name}], expenses, ledgerTotal, nextCursor, balances}`
- each expense: `{id, groupId, description, amount, paidBy, paidByName, splitType, date, splits:[{userId,amount,name,percent?}]}` (`percent` only on `percent` expenses), ordered by `date DESC, id DESC`
- `expenses` is the whole ledger unless the route got `?limit=N` (1–500): then only the newest N, and `nextCursor` (else null) continues it through `GET /groups/{id}/ledger`. `ledgerTotal` = every entry (expenses + payments). `balances` always come from every entry (see "Ledger paging" below). Every route answering with a Group takes the same optional `limit`; without it the answer is what it always was.
- `balances`: `{net:[{userId,name,amount}], transfers:[{from,to,amount,fromName,toName}]}`

| Method & path | Body / query | Returns / rules |
|---|---|---|
| GET `/users` | – | Picker list, for everyone. Syncs from HA first, using the persons cache and fetching **outside** the lock. Returns `[{id,name,createdAt,disabled}]` ordered by `disabled, name NOCASE` |
| GET `/admin/users` 🔒 | `refresh?` | Admin → Users. Same sync and order; `[{id,name,createdAt,disabled,haEntityId,groupCount,receiveNotifications,notify:["notify.x"],ha:{known,person,personName,phones:[{label,service,tracker}]}}]` (`ha` = `people_admin.ha_person_json(ha_user_id)`). `?refresh=1` re-reads Home Assistant's people first ("Check Home Assistant again") |
| GET `/admin/notify-services` 🔒 | `refresh?` | `people_admin.notify_services`: `{available, error, services, entities}` (a 200 with `available:false` when HA can't be asked) |
| POST `/admin/users/{id}/notify` 🔒 | `{service}` | 201 with the person (as in the list). `"x"` → `"notify.x"`; 422 for a bad name or more than 10; 404 unknown person |
| DELETE `/admin/users/{id}/notify/{service}` 🔒 | – | The person; 404 if not assigned |
| POST `/admin/users/{id}/notify/test` 🔒 | – | `{results:{"notify.x": ok}}` to every phone and extra service; 400 when there is none, 429 within 10 s for the same person, 502 when HA accepted none |
| GET `/me/prefs` | – | `{linked, name, receiveNotifications, reachable, notifyCharges, notifyPayments, haConnected}` for the caller, matched by `linked_user_id` (HA user id → `users.ha_user_id`, else via the cached Person list; **never** by name; disabled people included). `reachable` = a phone or extra service reaches them |
| PUT `/me/prefs` | `{receiveNotifications: strict bool}` (`extra="forbid"`) | Same shape. 404 when the login isn't linked to a Splitpot person; 422 for anything else |
| PATCH `/users/{id}` 🔒 | `{disabled}` | User object. 404 `"User not found"` if unknown. Logs only when the value changes |
| GET `/groups` | – | `[{id,name,createdAt,isDefault,memberIds,memberNames,expenseCount}]` ordered by `created_at`; the count excludes payments |
| POST `/groups` | `{name, memberIds}` | 201 `{id,name,createdAt,memberIds}`. Name is trimmed and required. Ids are deduplicated; needs at least 1; 400 for unknown or disabled ids |
| GET `/groups/{id}` | `limit?` | Group, or 404 |
| GET `/groups/{id}/ledger` | `limit` 1–500 (default 20), `cursor?` | `{expenses, nextCursor, total}`: the entries after `cursor` (none = from the newest), newest first. 400 for a malformed cursor, 404 for an unknown group |
| POST `/groups/{id}/members` | `{userId}`, `limit?` | Group. 404 for an unknown group; 400 for an unknown or disabled user. Idempotent: re-adding an existing member does nothing |
| PUT `/groups/{id}/default` | `{isDefault}`, `limit?` | Group. Setting `true` clears every other group's flag in the same transaction |
| DELETE `/groups/{id}` 🔒 | – | 204, or 404. Cascades, logs, pushes sensors |
| POST `/groups/{id}/expenses` | `ExpenseCreate` | 201 with the expense object (`splits` without names). 404 for an unknown group. Queues `notify_new_charge` as a background task (§7.1) |
| PUT `/expenses/{id}` | `ExpenseCreate`, `limit?` | Group. 404 for an unknown expense; 400 if it is a payment. Keeps the stored date when the submitted day equals the stored local day; otherwise re-resolves it. Replaces all splits |
| POST `/groups/{id}/payments` | `{fromUserId,toUserId,amount,date?}`, `limit?` | 201 with the Group. Queues `notify_payment` (§7.1) |
| DELETE `/expenses/{id}` 🔒 | – | 204, or 404. Works for expenses and payments |
| GET `/events` | `limit` 1–300, default 50 | `[{id,type,message,groupId,groupName,actor,createdAt}]`, newest first, joined to the current group name |
| GET `/transactions` | `period=week\|month`, `offset` (int, 0 = current, negative = past) | `{period,offset,label,start,end,total,count,expenses:[{id,groupId,groupName,description,amount,paidByName,date}],breakdown:[{label,amount}]}`. Breakdown is by weekday Mon–Sun for a week, or by group name (descending) for a month. Excludes payments |
| GET `/dashboard` | – | `{peopleCount (all users incl. disabled), groupsCount, expensesCount, overallNet:[{userId,name,amount}], week:{total,count}, month:{total,count}}` |
| GET `/whoami` | – | The shared contract (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`) `{haUserId, haUsername, haDisplayName, nameSent, isAdmin, displayNameOnly, adminEntries, noAdmin, viaIngress, notifyLinked, extras: [notify_row]}` (the shared "Phone linked for notifications" row: a phone or service reaches the caller's linked person) plus the app's own `userId`, `sensorSyncEnabled`, `haConnected`. `sensorSyncEnabled` = `haConnected` (a `SUPERVISOR_TOKEN` exists) AND the `ha_sync_enabled` setting. `adminEntries` is a count only; the list is never sent. `noAdmin` = `admin_users` is empty. Always describes the real caller. `userId` = the enabled Splitpot user whose `ha_user_id` equals `X-Remote-User-Id` (lower-cased); else the one found via the cached Person list's `user_id` → `ha_entity_id`; else the single enabled user named like the display name; else null |
| GET `/config` | – | `{currency}` — the current App setting (the frontend re-reads it on every Dashboard/group load) |
| GET `/admin/settings` 🔒 | – | The shared payload: `{values:{ha_sync_enabled,sync_interval_minutes,currency}, defaults:{…}, meta:{key:{label, help, group, kind, restartRequired:false, min?, max?, enabledIf?, maxLength?, suggestions?, …}}, groups:[{id, label, help}] (money, ha)}` |
| PUT `/admin/settings` 🔒 | any subset of the keys | Same shape as GET. 422 `{"detail": "Currency code: XQZ isn't an ISO 4217 currency code"}`-style message for a bad value, a wrong type or an unknown key (then nothing is saved). After saving (lock released, connection closed): `ha_sync_enabled` true→false → `remove_balance_sensors()`; false→true, or the currency changed while on → `push_balances_to_ha()` |
| GET `/admin/backup-db` 🔒 | – | Consistent snapshot made with `sqlite3.Connection.backup()` (`db_core.snapshot`) under the lock, not a file copy (WAL mode). Sent by `backup_core.send_file` as `application/vnd.sqlite3`, filename `splitpot-backup-YYYYmmdd-HHMMSS.db` (local time, `backup_core.file_name`). The temp file is deleted after the response |
| POST `/admin/import-db` 🔒 | multipart `file` | `{status:"ok"}`, or 400 for a non-SQLite file, a failed `integrity_check`, or any of the six core tables missing (`app_settings` is optional) |
| GET `/groups/{id}/export.csv` | – | Everyone. Every expense and payment of the group, oldest first, as a CSV (`csv_export.to_bytes`, UTF-8 BOM, `Cache-Control: no-store`, attachment `<group name> - YYYY-MM-DD.csv`): Date, Type, Description, Amount, Currency, Paid by, Split, then one "<name> share" column per member (and per former member with shares). **Formula guard** (`csv_export.text`): a text cell starting with `=` `+` `-` `@`, tab or CR gets a leading `'` — the Description and Paid by cells and the "<name> share" column headers (the headers are new); numbers never |
| GET `/health` | – | `{ok:true}`. Under `/api`, so it also needs the id header. No container healthcheck is configured |

🔒 = `require_admin`. The backend runs `push_balances_to_ha()` after the lock is released, following: add expense, edit expense, record payment, delete expense, delete group.

**Ledger paging** (`ledger_page`, `encode_cursor` / `decode_cursor`, `group_net`):
- Order: `ORDER BY date DESC, id DESC` — `date` compares as a string (a bare day sorts below that day's timestamps, as before) and the id breaks ties, so the order is total. Index `idx_expenses_group_date (group_id, date, id)`.
- Cursor: the last returned row's `(date, id)` as base64url JSON, opaque to the browser. The next page is `WHERE group_id = ? AND (date < d OR (date = d AND id < i))`, `LIMIT limit + 1` (the extra row only tells whether there is a next page, so a page that ends exactly at the last entry has `nextCursor: null`). Entries added later with a newer date land above the cursor and never shift or repeat a page; one back-dated below the cursor shows up in a later page. A well-formed cursor that matches no row is simply a position.
- Balances never come from a page: `group_net` sums `ROUND(amount·100)` per payer and per split person over **every** entry in SQL (integer cents, so the order doesn't matter), then `settle_up` (the greedy matching, shared with `compute_balances`). Nets list members first in their order, then anyone else with an entry. `GET /groups` counts, the delete-group confirmation (`ledgerTotal`) and the CSV export (unpaged `serialize_group`) cover every entry too.

**Restore flow:**
1. Stream the upload to a temp file in `DATA_DIR` (`backup_core.receive`). `/tmp` is on another filesystem, so `os.replace` would fail with EXDEV.
2. Validate the file (`db_core.validate_file`, same 400 messages).
3. Under `_lock`: if the backup has no `app_settings` table (an older database), copy the current `app_settings` rows first; then `backup_core.restore_file` (`db_core.swap_in`): `wal_checkpoint(TRUNCATE)` (errors ignored), `os.replace` the file over the DB, and delete the `-wal`/`-shm` sidecar files.
4. **Re-run `init_db()`** (the `migrate` step of `restore_file`) so an older backup is migrated immediately (this also clears the settings cache), then re-insert the copied settings rows, if any. A backup that has `app_settings` brings its own settings.
5. Clean up the temp file in a `finally` block.

## 7. Home Assistant & external integrations

All HA calls go to `http://supervisor/core/api/…` with `Authorization: Bearer $SUPERVISOR_TOKEN`, using blocking `urllib` (sensor calls go through `_ha_call(method, path, body)`, which tests replace with a fake). The notification calls (§7.1) go through the shared `common/ha_client.py` with `app/config.py`'s token and base URL instead. Failures are logged as warnings and never raised. None is made while holding `_lock` or an open DB connection. Without a token every HA call is a no-op, so local runs and tests don't need HA.

| Call | Timeout | Use |
|---|---|---|
| GET `/config` → `time_zone` | 10 s | Once at startup. If it fails (or there's no token), the app stays on UTC |
| GET `/states` | 5 s | `fetch_ha_persons`: keeps `person.*` entities with `attributes.user_id`. Name is `friendly_name`, falling back to a title-cased object id |
| POST `/states/sensor.splitpot_balance_<slug>` | 5 s | `{state: round(net,2), attributes:{friendly_name:"<Name> Splitpot Balance", unit_of_measurement: <currency setting at push time>, icon: mdi:cash-plus (≥0) / mdi:cash-minus}}`, built by `balance_sensor_states(conn)`; the HTTP calls happen after the connection is closed |
| GET `/states`, then DELETE `/states/<entity_id>` | 10 s / 5 s | `remove_balance_sensors()` when sync is switched off: deletes every `sensor.splitpot_balance_*` HA lists plus one per known user (the latter alone if the list can't be read). A 404 counts as removed |

- **Person sync** (`sync_users_from_ha`):
  - Upserts users by `ha_entity_id`: renames when the name changed, stores `ha_user_id`, inserts new people (IntegrityError ignored), and never deletes.
  - Runs on `GET /api/users` and `GET /api/admin/users` only.
  - `cached_ha_persons()` caches for 300 s under its own lock. An empty result (an HA outage) is not cached.
- **Sensors:**
  - `push_balances_to_ha` / `remove_balance_sensors` post and remove through `main.SENSORS`, a `sensor_publisher.Publisher` (`app/common/sensor_publisher.py`) on `ha_set_state` / `ha_delete_state`, and count what it pushed or removed; the `periodic_sync` loop is unchanged.
  - One sensor per person in `compute_overall_net`, i.e. anyone in at least one group. The slug is `re.sub('[^a-z0-9]+','_', name.lower()).strip('_')`, or `user` if that is empty.
  - Only sent when `sensor_sync_enabled()` (token AND the live `ha_sync_enabled` setting): after every expense/payment/group change, by the loop, and by the settings route.
  - Renaming a person creates a new sensor; the old one goes stale until sync is switched off (removal catches it via the state list) or HA restarts (REST-created states aren't persisted by HA).
- **Time zone** (`set_timezone`, `tz()`, `local_now()`, backed by `main._ZONE`, an `ha_time.Zone` from `app/common/ha_time.py`):
  - Starts on UTC; `load_ha_timezone()` reads it at startup (`ha_time.load`); `set_timezone(name)` switches to HA's zone and keeps the current one for an unknown name.
  - `parse_date`: a bare `YYYY-MM-DD` is midnight in that zone; a timestamp without an offset is treated as UTC.
  - `week_range(offset)`: Monday 00:00 local, 7 days long. Label is `"%b %-d – %b %-d, %Y"`.
  - `month_range(offset)`: the calendar month in local time, computed with year·12+month arithmetic. Label is `"%B %Y"`.
  - Ranges are half-open: `[start, end)`.

### 7.1 Phone notifications

Shared code as in the other apps: `common/ha_notify.py` (sending: notify action, or `notify.send_message` for a notify entity; never raises), `common/ha_people.py` (each Person's Companion-app phones, read with one `POST /api/template`, cached; refreshed at startup and every 5 minutes by `ha_people.loop`), `common/people_admin.py` (Admin → Users routes' shared parts), `common/static/people.js` (the page). They read `app/config.py` (`SUPERVISOR_TOKEN`, `SUPERVISOR_CORE_API`, `INGRESS_PANEL`) at call time; `app/db.py` gives them `get_conn`.

- **Who is reached** (`person_services(conn, user_row)`): `ha_people.phones_for(users.ha_user_id)` (the phones HA links to the person) + the person's `user_notify` services, deduplicated. Splitpot's `users.id` is its own uuid, so the two lookups use different keys and the app combines them itself instead of `ha_notify.services_for` (which assumes users.id = HA user id). A member without a linked HA login and without an extra service is simply not told.
- **New charge** (`POST /groups/{id}/expenses`; when `notify_charges` is on and there is a token): a FastAPI background task (`notify_new_charge(expense_id, X-Remote-User-Id, actor)`) runs after the response, re-reads the entry, and notifies everyone involved — the payer and everyone in `expense_splits` — **except the person who added it** (`linked_user_id` of the request's HA user id; an admin who isn't a Splitpot person excludes nobody). Each recipient is skipped when `disabled`, `receive_notifications = 0`, or no service reaches them. Text (`charge_notices`): `"<actor> added '<desc>' (<money>) to <group>, paid by <payer|you>. Your share: <money>."` (the payer without a share: `"… paid by you. You're not in the split."`). Amounts in the currency setting at send time.
- **Settle-up payment** (`notify_payment`; needs `notify_charges` and `notify_payments`): the payee and the payer, except whoever recorded it: `"<payer> paid you <money> in <group> (a settle-up payment)."` when the payer recorded it, `"<payee> recorded your payment of <money> to them in <group>."` when the payee did, otherwise `"<actor> recorded <payer> paying you …"` / `"<actor> recorded you paying <payee> …"`.
- **Not notified** (decided): edits, deletes, group and member changes — the activity log shows them, and an edit storm would be noise.
- Title `"Splitpot"`; `data = {url, clickAction}` = `INGRESS_PANEL + "#/group/<id>"` (`/hassio/ingress/<slug with _>`, from the container's `HOSTNAME`, as in Arcade/Todo; no data outside Home Assistant). The page opens `#/group/<id>` on load or hashchange when that group exists.
- **Quiet and safe**: no token → nothing is tried (no warning per charge); HA unreachable → `ha_notify` logs a warning, the request already succeeded; any other error in the task is logged (`logger.exception`), never raised. No DB connection is open while Home Assistant is called (`send_notices` closes it per person before sending).
- At startup (with a token) `ha_people.refresh_blocking` and `ha_notify.check_targets_blocking` (warns about assigned services HA doesn't have) run once in a thread.

### 7.2 The household apps bus and the Household Assistant (`app_messages.py`, `tools.py`)

- `app_messages.start()` in the lifespan learns the sidebar page into `config.SIDEBAR_PAGE` (the shared
  `assist_tools.sidebar_page`: the Supervisor's `addons/self/info`, else `HOSTNAME`; `/<8 hex or local>_splitpot`
  only — not the admin's `/hassio/ingress/…` page the notifications use) and starts the shared `app_bus.py` (its own
  WebSocket; the outbox thread only with a token). The bus's tables (`bus_outbox`, `bus_seen`, `bus_apps`) are made
  by `app_bus` itself.
- It answers the **Household Assistant** (`assist.tools.list`, `assist.tool.call`; the shared `assist_tools.py`,
  APP_MESSAGES_SPEC §6.6; HOUSEHOLD_ASSISTANT_SPEC §4.2). `requested_by` (an HA user id) is matched to a Splitpot
  person by `users.ha_user_id` only, never by name; unlinked or disabled → `nack not_allowed no_access`. Only the
  groups that person is a member of (`group_members`), or the one named in `group` (any case; else `nack not_found
  group`).
  - `splitpot.balances` (`group?`): per group the settle-up transfers (`settle_up(group_net(…))`, as the group
    page) as `{group, from, to, amount}`, and the person's overall net in the text.
  - `splitpot.recent` (`group?`, `days?` 1–90): each group's newest 20 entries (`ledger_page`), merged newest
    first, the first 20 kept (`more` when there were more) as `{date (local day), group, what ("payment" for a
    settle-up), paid_by, amount, your_share}`.
- **Off by default** (App settings → **Answer the Household Assistant**, `assistant_answers`: money is private); a
  person can also turn it off on My settings (`users.assistant_ok`, default on; `PUT /me/prefs {assistantOk}`).
  Otherwise `nack not_allowed` (`off` / `person_off`). Links are the sidebar page only (no sub-path routes).

## 8. Background jobs

- `lifespan` (not `on_event`):
  - Fetches the HA time zone (`await load_ha_timezone()`).
  - If there is a HA connection (`SUPERVISOR_TOKEN`; on/off is checked inside the loop so it can be switched on later), starts `periodic_sync()`: sleep 10 s, then every tick (at most `SYNC_TICK_SECONDS` = 30 s) re-read `ha_sync_enabled` and the interval:
    - **on**: if nothing was pushed yet (startup, or just switched back on) or `seconds_until_next_sync(last_run, now) = max(0, last_run + sync_interval_minutes·60 − now)` is 0, set `last_run` and `to_thread(push_balances_to_ha)`. So a new interval applies from the next cycle without a restart (a shorter interval doesn't wait out the old sleep).
    - **off**: on the first off tick (startup with sync off, or just switched off) `to_thread(remove_balance_sensors)` once, then idle.
    - An exception is logged and the loop carries on. `sleep` and `clock` are injectable for tests.
  - With a HA connection it also starts `ha_people.loop` (job `"ha_people"`: the people and their phones every 5 minutes) after reading them once (and checking the assigned notify services) at startup.
  - The loop is started and stopped by the shared Jobs runner (`app/common/housekeeping.py`, job `"sensor_sync"`); shutdown cancels the task and waits for it.
- Notifications are FastAPI background tasks after the request (§7.1), not a loop. No other scheduled work. Logging is set up by `housekeeping.setup_logging()` (INFO, `"%(asctime)s %(levelname)s %(name)s: %(message)s"`), logger `splitpot`, and no `print`.

## 9. Frontend

Single page. Sections `.view` are toggled with `.active` by `showView(name)`.
- The tabs are `dashboard | groups | me (My settings, bell icon, for everyone) | admin`. `#adminTabBtn` (shield icon, "Admin") is `hidden` unless `/whoami` says `isAdmin`.
- `group` (group detail) and `whoami` are reached without a tab and leave no tab highlighted.
- Only the Admin area has URLs: `#/admin/settings`, `#/admin/users`, `#/admin/storage` (`#/admin` or an unknown tab → settings). The one other address is a notification's `#/group/<id>`: on load (instead of the default group) or on hashchange it opens that group if it exists; `showView` then drops the hash. The short forms `#/people`, `#/users`, `#/storage` and `#/settings` redirect there with `history.replaceState`. Navigation never adds history entries: `openAdmin` and `showView` (dropping the hash when leaving Admin) use `history.replaceState`; `hashchange` only handles a typed address or `BackNav.go`.
- **Back**: `common/backnav.js` is loaded before `app.js` (same `?v=`) and `BackNav.init` runs once after the first page renders (`init().finally(initBackNav)`). Start page = the default group if one is set, else the Dashboard (computed live from `state.groups`). `atHome` = that view is active (and, for a group, `currentGroupId` matches); `goHome` = `openGroup(default)` or `showView('dashboard')` + `loadDashboard()`; the only layer is the expense form in edit mode (`state.editingExpenseId` while the group view is active), closed with `resetExpenseForm()`. So Back: cancels an edit → start page (in one step) → leaves for HA. `confirm()`/`prompt()` are native and not layers.
- All fetches are **relative** (`fetch('api' + path)`, `href="api/admin/backup-db"`) so they work under the Ingress path prefix.
- Shared browser helpers: `common/ui.js` (`window.UI`) loads first; `api()` is `UI.makeApi({…})` with the app's own options (raw fetch options), `escapeHtml` is `UI.escapeHtml` after `String()`. `toast(msg, isError)` = `UI.toast` into `#toastRoot` (fixed, bottom centre; above the tab bar on a phone). Script order at the end of `index.html`: `common/ui.js`, `common/settings.js`, `common/people.js`, `common/backnav.js`, `common/whoami.js`, `app.js`.
- `api()` sends JSON by default and throws `detail` on errors. For the multipart upload it is called with `headers: {}`.

**Shell & theming**
- `<title>Splitpot</title>`. Google Fonts: Source Serif 4 (headings) and Inter (body).
- Theme boot is `common/theme-boot.js` (an external script in `<head>`, before the stylesheets `common/themes.css`, `common/settings.css`, `style.css`; no inline script). It applies `localStorage.theme` (`midnight` (default) `|slate|daylight|auto`; the old `paper` maps to Midnight) to `html[data-theme]` (always the resolved theme; Auto = Daylight on a light device, else Midnight) and `sidebarCollapsed==="1"` to `html[data-sidebar=collapsed]`, so nothing flashes on load. The Theme `<select>` is filled by `HouseholdTheme.bindSelect()`.
- CSS tokens: the surfaces, text and state colours come from `common/themes.css`; Splitpot's `--paper --paper-dim --ink --ink-soft --line` map onto the shared `--bg --panel --text --text-dim --border`. Its own, per theme in `:root, [data-theme="midnight"]`, `[data-theme="slate"]`, `[data-theme="daylight"]`: `--moss --moss-dark --rust --gold --line-strong` (and the accent, moss: Midnight moss `#4F9468` / rust `#E28259` / gold `#D8AC4E`).
- Layout is `.app` (a **class**, so use `.app` selectors, never `#app`) as a flex row:
  - `.sidebar` is 220 px, sticky, full height. It contains the brand ◆, nav with inline stroke SVG icons, and a footer holding the "Signed in as" chip (`#sidebarUser`) and the Theme `<select>`.
  - `main` is `max-width: 880px`.
  - Collapse button: 68 px icon rail, stored in `localStorage.sidebarCollapsed`; desktop only.
  - At ≤640 px the sidebar becomes a fixed bottom tab bar (brand, footer and collapse button hidden) and tap targets get bigger.
- **No admin yet** banner (`#noAdminBanner.setup-banner`, `role=alert`): an empty container directly under the topbar, outside every `.view`, filled by `HouseholdWhoami.fillNoAdminBanner` (`common/whoami.js`), so it shows on every page and to everyone while `/whoami` says `noAdmin`: "No admin yet — add your Home Assistant user name (**<login name, else user id>**) to `admin_users` on the app's Configuration tab, save, and restart the app." plus a **How the app sees you** link button. The name is set with `textContent`.
- Topbar (`.topbar-right`): a 👤 `#whoamiBtn`, plus the "Acting as" `<select>` listing enabled users. "Acting as" is client-only: it just pre-selects **Paid by**. It is auto-set (before the first group/Dashboard renders) to `/whoami`'s `userId`; if that is null, to the one enabled user whose name equals the HA display name (case-insensitive). `defaultPaidBy()` sets `#expPaidBy` to "Acting as" when it's an option there and no expense is being edited; it runs on `#actingAs` change, at the end of `resetExpenseForm`, and in `renderGroupDetail` (which keeps the current payer while editing).

**init order:**
1. `loadConfig()` = `GET /config` (currency; also re-run at the start of every `loadDashboard` and `openGroup`, so a currency change reaches open pages without a reload)
2. `GET /whoami` (`isAdmin`, `sensorSyncEnabled`, `haConnected`, before any Delete button renders); unhide `#adminTabBtn` for admins; show the No admin yet banner if `noAdmin`
3. `loadUsers` (`GET /users`, the picker list)
4. `linkHomeAssistantUser(who)`: set the sidebar name, pick "Acting as" (so the first group opens with Paid by = you)
5. `loadGroups`
6. If the hash is an Admin route (or a short form), open it; else if a default group exists, open it; otherwise `loadDashboard`

Clicking a tab reloads Groups or Dashboard; Admin opens its last tab.

**Dashboard**
- Stat tiles: Groups, Users, Spent this week, Spent this month (via `money()`, like every amount).
- Overall balance: rows labelled "is owed" / "owes" / "settled up" (|x| ≤ 0.004), with a note that this is not a payment suggestion.
- Transactions:
  - Week/Month toggle, scoped as `#periodToggle .split-btn`.
  - ← / → navigation; → is disabled at offset 0.
  - Summary "X across N transactions".
  - Bar list scaled to `max(1, …)`.
  - Rows with date, group and payer.
- Activity log: newest 50, a `.event-dot.<type>` colour per type, and `timeAgo()` (year/month/day/hour/minute/"just now").

**Admin** (`#view-admin`; admins only)
- Non-admins get `#adminDenied`: "Only admins can open this page." plus a link to "How the app sees you". Admins get `#adminBody`: a `.admin-tabs` row (`role=tablist`, scrolls sideways if narrow) with **App settings | Users | Storage**, each panel `hidden` unless selected.
- **App settings**: drawn by `common/settings.js` (`SettingsPage.render`) from the payload's `meta` and `groups`: one card per group (**Money**, **Home Assistant**), a help line with the range and default under each setting, Save settings / Discard changes at the bottom ("N unsaved changes"), wrong numbers flagged at the field before saving. Money: Currency code (text, `maxlength 3`, a pick-list of common codes, and the app's live preview "Looks like: €1,234.50" via `money(1234.5, code)`). Home Assistant: a "Sync balances to Home Assistant" switch (with the app's `#syncNoHaNote` "Splitpot has no Home Assistant connection right now…" when `haConnected` is false) and Sensor sync interval (number 1–60, greyed out while the switch is off — `enabledIf`). The intro says only `admin_users` stays in the Configuration tab. Save sends the changed keys (`PUT /admin/settings`); the server's 422 `detail` is shown; on success "Saved." and `state.currency` is updated so the next render uses it.
- **Users**: `GET /admin/users` drawn as the shared people list (`common/people.js`, `PeoplePage.render`): one card per person with "In N groups", a Disabled badge and "Disabled — hidden from selection", the HA entity id, plus Enable/Disable (`PATCH /users/{id}`, then reload this list and the picker list); an intro paragraph on where phones come from; **Check Home Assistant again** (`?refresh=1` on both lists); and the shared inline notify editor per person (Phones from HA, Also = extra services with ✕, Add from `GET /admin/notify-services` or typed, **Send a test**; "· turned notifications off" when the person opted out). Empty-state copy points to HA Settings → People.
- **Storage**: see below.

**My settings** (`#view-me`, everyone): `GET /me/prefs` → one card "Notifications" with **Receive notifications** (the shared `PeoplePage.accessSwitch`; `PUT /me/prefs`, toast "You'll get notifications" / "Notifications off for you", reverts on error) and the sentence of what it covers (mentions settle-ups only while `notifyPayments`). A warning line when: the login isn't linked to a Splitpot person (switch disabled), the household switch is off ("an admin can switch them on in Admin → App settings"), or no phone reaches them. A "How the app sees you" link in the intro.

**Groups**
- Create form: name (`maxlength 60`, a client-side limit only) and a chip picker of enabled people.
- List rows: a ☆/★ star (`data-toggle-default`, checked before the open-group handler), name, "Default" badge, member names, expense count.

**Group detail**
- Header: title, `#groupDefaultBtn` ("☆ Set as default" / "★ Default group"), `#groupExportBtn` ("⬇ Export CSV", a link to `api/groups/<id>/export.csv`, for everyone; greyed out while the group has no expenses), and `#groupDeleteBtn` ("Delete group", `hidden` unless admin). Delete asks for confirmation, showing the expense/payment count, then returns to the Groups list.
- Member pills include disabled members, marked "(disabled)".
- Two-column layout:
  - Left: expense form, all selectors scoped to `#addExpenseForm .split-btn` (a bare `.split-btn` also catches the dashboard toggle). Fields:
    - description (`maxlength 80`)
    - amount (`step 0.01`, `min 0.01`)
    - Paid by (enabled members)
    - Date (defaults to today, `max` = today)
    - Equal/Custom toggle (handlers scoped to `#addExpenseForm .split-btn[data-split]`), participant chips, and for Custom a **By amount | By percentage** sub-toggle (`#customModeToggle`, `data-custom-mode`, `state.customMode`, default amount) plus one input per person (`data-custom-input`; typed values survive a participant change in the same mode).
    - By percentage: a `%` input per person (`step 0.01`, 0–100) with that person's amount beside it (`data-share-for`, same largest-remainder maths as the server, in `money()`), and `#customSplitSummary`: "Total X% · remaining Y%" (`.off` = rust until exactly 100.00) and **Split the rest evenly** (fills the empty rows with what's left, in hundredths, the last rows taking the extra; disabled with no empty rows or nothing left). Submitting with a total other than 100% shows "Percentages must add up to 100%" without calling the server; typing in the rows clears the error. Sends `splitType:'percent'`, `splits:[{userId, percent}]`.
  - Right: Balances, i.e. net rows plus transfer rows, each with a **Record payment** button. It opens a `prompt()` pre-filled with the suggested amount; partial payments are allowed; the date sent is today.
- Ledger (`expenseRow(e)`, built with `UI.h`; `data-expense-id` on each `<li>`, amount and buttons in `.row-actions`; on a phone the row wraps so the buttons sit under the text):
  - **Paging**: `refreshGroup(keep)` loads `GET /groups/{id}?limit=n` with n = 20 when a group opens, or — after add/edit/delete/payment (`keep`) — as many as are showing (20–500), so the list doesn't jump back. Under the list `#ledgerMore`: "Showing X of Y entries" (`ledgerTotal`) and **Load N more** (`GET /groups/{id}/ledger?limit=20&cursor=…`, appended; ids already shown are skipped; hidden when `nextCursor` is null; "Loading…" while busy; a reply for a group no longer open is dropped). Edit/Delete find the entry among the loaded rows (the row clicked is always loaded). Nothing in the page computes from the loaded rows: balances, the delete-group count (`ledgerTotal`) and Export (`ledgerTotal` > 0) come from the server.
  - Expense rows show date, payer, "split Name 1.23 · …" (percent expenses: "split by percentage · Name 60% ($30.00) · …"), **Edit**, and **Delete** (admin only, with confirmation).
  - Payment rows (`.payment-row`, italic) show "💸 A paid B · settle-up payment", with no Edit button.
  - Payer colours: the payer's name is `<span class="payer pcN">` and the `<li>` gets `payer-row pcN` (3 px left border). N = the user's index in `state.users` sorted by `createdAt` then id, mod 8. `--pc0…--pc7` are defined per theme (light pastels for Midnight/Slate, dark shades for Daylight). The Dashboard transaction list uses the same classes.
- Edit mode: loads the row into the form (a `percent` expense opens as Custom → By percentage with its saved percentages), which switches to "Edit expense" / "Save changes" and shows Cancel. A successful save or Cancel resets the form. Deleting the expense being edited also resets it.
- Dates are displayed with `parseWhen()`, which reads bare `YYYY-MM-DD` as a **local** date (`new Date(y, m-1, d)`, not UTC). Amounts use `money()` = `Intl.NumberFormat(currency)`, falling back to `"12.50 XYZ"`.

**How the app sees you** (per `WHOAMI_PAGE_SPEC.md`; drawn by the shared `HouseholdWhoami.panel()` from `common/whoami.js`; one extra row from the server, "Phone linked for notifications" Yes/No with how to link one): `.kv` rows showing
- User name (or "not sent"), with a copy button
- User id (`<code>`), with a copy button
- Display name "(not used for matching)"
- Administrator in this app (Yes/No)
- Names in admin_users (a count)

There is no "Opened through Home Assistant" row. Advice text, in priority order:
1. You are an administrator.
2. `displayNameOnly`: replace your display name with your login name or id, then restart.
3. The list is empty: add your user name to `admin_users`, save, restart.
4. No match: add your name or id exactly, then restart; case doesn't matter.

**Storage** (Admin tab)
- "Download database (.db)" link.
- Restore: file input (`accept=.db`) and an Import button, behind a `confirm()`. On success it shows a status message and reloads after 1.2 s.

## 10. Invariants & pitfalls

- Never do network I/O while holding `_lock` or an open connection. Fetch persons first; push sensors after commit.
- Log each event inside the same transaction as its change, before `commit()`.
- Every id an expense or payment references must be a member of that group. Validate duplicates yourself; the composite primary key would otherwise surface as a 500.
- Bare dates are local days in **both** backend (`parse_date`) and frontend (`parseWhen`). `new Date('YYYY-MM-DD')` is UTC midnight, which is the previous evening in the Americas.
- `expenses.date` sorts as a string. On the same day, a bare date sorts below full timestamps.
- Payments move balances but are never counted as spending. Filter with `split_type != 'payment'` in every total and count.
- Do not run the greedy settle-up on the cross-group overall net.
- Never compute balances, totals or counts from a ledger page in the browser: a page is display only.
- A notification must never make a request slow or fail: send from a background task, check the switch and the token first, catch everything.
- Match people for notifications and My settings by HA user id only, never by display name.
- The HTML shell (`/` and `*.html`) is sent with `Cache-Control: no-cache, no-store, must-revalidate` and `Pragma: no-cache` (`web_security`). CSS and JS are cache-busted with `?v=<version>`.
- The collapse button's `›`/`‹` is swapped in JS only; don't also rotate it in CSS or the arrow points the wrong way.
- `ADMIN_NAMES` is read once, at import time. App settings are read when used (`get_setting`), never cached in module globals; don't reintroduce a `CURRENCY` constant.
- Never read `ha_sync_enabled`/`sync_interval_minutes`/`currency` from options.json; they exist only as App settings.

## 11. Build, test & release

- Tests: from the app folder run `python3 -m unittest discover -s tests` with `requirements-dev.txt` installed (standard library otherwise: no PyYAML or Pillow).
  - `_env.py` must be imported first (built on `common_tests/env.py`): it chdirs to the app folder, sets a temp `DATA_DIR` and removes `SUPERVISOR_TOKEN`.
  - The client is `TestClient(app, client=("127.0.0.1", 12345))` so requests pass the ingress check (`common_tests/ingress.py` has the same as `ingress_client` and the header helpers); `ADMIN_NAMES` is set to `{"adminy"}`.
  - `tests/common_tests/` (copies from `common/tests/`): `env.py`, `ingress.py`, `packaging_core.py`, `test_shared_copies.py` (no copy was edited) and the shared modules' own tests (`test_whoami_shared`, `test_auth_core`, `test_db_core`, `test_settings_core`, `test_web_security`, `test_backup_core`, `test_sensor_publisher`, `test_csv_export`, `test_people_admin`), plus `fake_ha.py`.
  - All people and households in the tests are invented data.
  - 99 tests of the app's own. `test_app.py` (23) covers: ingress 403/401; admin by login name vs display name alone; members-only splits; duplicate splits; negative, Inf, NaN and 0 amounts (422); date rules; group member validation; bare date = local midnight; edit keeps the timestamp; payments settle debts but aren't spending, can't be edited, and only admins can delete them; admin-only deletes (403/204/404, logged); non-admins can still add, edit and pay; backend `money()` with EUR/unknown codes; the persons cache (outages not cached); backup → restore of an older schema without `is_default`, then set-default works; a junk upload returns 400; `/whoami.userId` by HA user id (not name), before the people list is synced, by a unique enabled display name only, never a disabled person; `/transactions` carries `paidBy`.
  - `test_percent_split.py` (9) covers: `percent_shares` rounding cases (the four above plus 99.99 at 60/40 and 1,000,000 at 0.01/99.99), percentages rounded to 2 dp, 0% rows dropped/all-zero rejected, and 3,000 random splits always summing exactly with no negative share and each within a cent of the exact value; the API: 400 for totals ≠ 100 / all zero / duplicates / non-members / amounts instead of percentages / empty, 422 for >100, negative, NaN, Infinity; percentages stored and returned (not on equal/custom rows); balances, dashboard, transactions, activity log and sensor states; 10.00 at 33.33/33.33/33.34; edit round-trip percent → percent → custom → equal → percent, and a rejected edit leaves the split alone; an older DB gets the `percent` column.
  - `test_admin_settings.py` (25) covers: settings GET/PUT admin 200 vs non-admin/display-name impostor 403; the response shape; partial update keeps the other key, currency normalised, `updated_by` stored, `settings_changed` logged; unchanged values not rewritten; 422 for out-of-range/wrong-type/`Infinity`/`NaN`/non-ISO/unknown keys (and nothing saved); currency change used at once by `/api/config`, the activity log and the sensor payloads (and pushed right away); `seconds_until_next_sync` with the new interval; `periodic_sync` with a fake clock picking up a change mid-wait (pushes at 10, 70, 130, 190 s) and surviving a failing cycle; config.yaml's `options:`/`schema:` and `translations/en.yaml` contain only `admin_users`, and an options.json with `ha_sync_enabled`/`sync_interval_minutes`/`currency` keys is ignored (defaults, no rows written); **No admin yet**: with `admin_users` empty `noAdmin` is true for everyone, nobody (not even the first visitor) gets admin APIs, it clears once a name is listed, and the page shell has the banner outside every view, wired to `/whoami`; admin-only `/admin/users`, `PATCH /users/{id}`, backup and restore, while pickers/groups/balances still work for everyone; restore brings a backup's own settings and keeps the current ones for a backup without `app_settings`; with a fake HA (`FakeHA` patches `_ha_call` and fails any call made while a DB connection is open): sync switch admin-only, instant push after changes, off removes every balance sensor (a stale renamed one too, nothing else) and stops all pushes, on pushes at once with the current currency, removal falls back to known users when the state list fails (404 = gone), nothing is called without a token (`haConnected` false), the loop follows the switch (push 10 s → remove 40 s → push 160/220/280 s) and removes once at startup when off; `ha_sync_enabled` validation.
  - `test_notifications.py` (22, the shared `common_tests/fake_ha.py`, one fake server per class) covers: off by default; everyone involved but whoever added it, with the message, share and `url`/`clickAction` link; an extra service from Admin → Users; the payer told when someone else adds, in the current currency ("You're not in the split."); custom splits and an actor who isn't a Splitpot person; opt-out and disabled people skipped; edits and deletes never notify; HA failing, no token (no requests at all), and an exception in sending never break adding; nothing sent while a DB connection is open; nobody with no phone; settle-ups to both people except the recorder (all three wordings) and both switches; `/me/prefs` GET/PUT, matched by login only (not a matching display name), strict validation; `/whoami.notifyLinked`; Admin → Users phones/services/choice, `?refresh=1`, add/remove/test (422/404/403/429/400), notify-services available or not; the startup service check; settings validation, `enabledIf`, the group and the log line; an old DB gets `receive_notifications` and `user_notify`.
  - `test_ledger_paging.py` (10) covers: an empty group; no `limit` = everything as before; order by date then id with ties on one day, walking every page size (1, 7, 20, 44, 45, 46) gives each entry once in order with no empty last page; a last page that is exactly full has no cursor; entries added meanwhile (newer, and ties on the cursor's day) neither repeat nor shift the next page; balances (equal to `compute_balances` over everything), the group list count and the CSV are unaffected by `limit`; every Group-returning route takes `limit`; bad cursor 400, limit 0/501 422, unknown group 404; cursor round trip; the index exists.
  - `test_security_headers.py` (2) covers: the CSP and `nosniff` on the page and the API, and no inline scripts or `on…` handler attributes in the page.
  - `test_packaging.py` (8; the checks come from `common_tests/packaging_core.py` with the app's own values) covers: config.yaml (version, `url`, `ingress: true`, `panel_admin: false`, no `ports`, options == schema == translation keys, empty `admin_users` default); every `?v=` equals the version; README/DOCS/Dockerfile/.dockerignore exist, `CHANGELOG.md` exists, starts with `# Changelog` and its newest (top) version heading equals the config.yaml version, `spec/` exists; no version numbers or history sections in README/DOCS/SPEC; `.dockerignore` entries; icon 128×128 and logo 250×100 (read from the PNG header); and no personal details (names, e-mail addresses, LAN addresses other than `192.168.1.10`, home region) in any text file of the app, the repository URL excepted.
- Install for testing: add the repository in **Settings → Apps → Install app → ⋮ (top right) → Repositories**, paste `https://github.com/sameerkotra/ha-apps`, **Add** (or copy the folder to `/addons/local/splitpot/` as a local app); then in **Settings → Apps → Install app** find Splitpot, **Install**, then **Start** on the Information tab, and check `/api/whoami`, Admin → Users and the sensors.
- **Release:** bump `config.yaml` `version` and the `?v=` values in `index.html` (`style.css`, `app.js` and the `common/` files) together (`test_packaging.py` checks they match). Describe user-visible changes in `DOCS.md` (the user guide) and in `CHANGELOG.md` (shown in Home Assistant's update dialog; its version heading must equal the config.yaml version). Bump the pinned dependencies together with the sibling apps.

## 12. Possible future work

- Remove group members and rename groups (deliberately absent today).
- Remove a renamed person's old balance sensor straight away instead of when sync is switched off or Home Assistant restarts.

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD: `sh -c "uvicorn … --no-proxy-headers"`), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
