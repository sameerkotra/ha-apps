# Household Todo — rebuild spec

This spec is enough to rebuild the app. Where it disagrees with the code, the code wins. Conventions shared with the sibling apps in the same repository (themes, admin determination, database export, the "How the app sees you" page) are summarised where they are used.

## 1. Purpose & scope

Household Todo is an ingress Home Assistant app for household tasks. It has no login and no local accounts: each person is their Home Assistant (HA) user.

- **Lists.** Shared lists are visible to everyone; personal lists are private to their owner.
- **Tasks.** Only a title is required. Optional: notes, a **link** (http/https URL), due date and time, priority, one **type**, one **place**, an assignee, a **checklist**, and a *completion required* flag.
- **Schedule items.** Recurring things that are never completed, such as trash day or fortnightly recycling. Individual dates can be skipped, moved or added, and each item can be mirrored to HA as a `binary_sensor`. An item may also be **for one person** — e.g. a yoga class every Tue, Thu & Fri, 18:00–19:00 — with a start/end time, a place, task-style reminders, optional privacy, and a sensor that is on only during the time window (§13). Any item may carry a **link**, like a task.
- **Calendar**, **Dashboard** (with workload cards) and a shared **Places** address book with optional estimated drive time from home.
- **Reminders** through HA notify, each opt-in per user: daily digest, weekly summary, per-task "N before due", and assignment pings — covering the user's tasks and the schedule items that are for them.
- **Admins** can act as another user, enable or disable users, and back up or restore the database.
- **Maintenance** (optional, §14): recurring house upkeep that is marked done, with suggestions, history, files and its own notifications.
- **Out of scope** (see §12): `todo.*`/`calendar.*` entities, recurring tasks (recurring *chores to tick off*), changing a list's kind, per-list ACLs, tags, and notification snooze or actions.

## 2. Stack & file layout

Python 3.12 on `python:3.12-alpine` + `tzdata`. FastAPI + uvicorn, raw `sqlite3` (no ORM), `/data/household.db` in WAL mode. Stdlib `urllib` in the threadpool for Home Assistant, Nominatim and OSRM. Plain HTML/CSS/JS frontend: no build step, no libraries.

```
household_todo/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore  .gitignore
├── translations/en.yaml   README.md  DOCS.md  CHANGELOG.md  icon.png  logo.png
├── spec/SPEC.md          # this file
├── app/
│   ├── main.py           # lifespan (init_db, HA tz, geocode home, notify check, the background jobs), routers, ingress-IP middleware, security headers (CSP), static mount
│   ├── config.py         # options.json → ADMIN_NAMES (other keys ignored); today()/now() in HA tz (config.ZONE)
│   ├── settings.py       # App settings declared on common settings_core: SETTINGS, GROUPS, hooks, drive_mode()
│   ├── auth.py           # get_current_user, get_acting_user, get_real_user_for_prefs, require_admin (on common auth_core)
│   ├── db.py             # SCHEMA, MIGRATIONS + _migrate, first-run seed, backup/validate/import (on common db_core)
│   ├── links.py          # clean_url (the http/https link rule), is_web_url
│   ├── recurrence.py     # pure rule maths
│   ├── schedule_logic.py # visibility, exceptions → effective dates, sensor state/payload (incl. timed windows), upcoming, calendar entries, exception validation
│   ├── taskview.py       # visibility SQL, TASK_SELECT, task JSON, sorts, default list
│   ├── reminders.py      # digest / weekly / task-reminder / schedule-reminder passes, assignment ping, loop
│   ├── ha_client.py      # thin: re-exports the shared client (app/common/ha_client.py: request(), post_state/delete_state) + load_timezone
│   ├── ha_sensors.py     # push/delete one, full sync, timed-window pushes, loop (on common sensor_publisher)
│   ├── housekeeping.py   # purge completed tasks, prune exceptions, the app-messages outbox, loop (common housekeeping.periodic)
│   ├── app_messages.py   # messages from the other household apps: todo.lists.list, todo.items.add (§15)
│   ├── geocode.py        # Nominatim (throttled, unit-strip retry), OSRM route, live home geocode (ensure_home_blocking); all behind the Drive times switch (on common geo)
│   ├── drive_time.py     # background cache warmer + compute_for_address()
│   ├── maint_catalog.py maintenance.py maint_files.py maint_notify.py   # Maintenance (§14)
│   ├── routers/          # me users (members + Admin → Users) lists tasks task_types places schedule calendar dashboard prefs admin (settings + storage) maintenance
│   ├── common/           # shared Python (copies): ha_notify, ha_people, whoami, ha_client, ha_time, housekeeping, auth_core,
│   │                     #   db_core, settings_core, people_admin, web_security, backup_core, sensor_publisher, geo, csv_export,
│   │                     #   app_bus, ha_ws
│   └── static/           # index.html app.js maintenance.js style.css
│       └── common/       # shared browser files (copies): theme-boot.js themes.css ui.js settings.js settings.css people.js backnav.js whoami.js
│                         #   connected-apps.js
└── tests/                # _env.py test_api.py test_core.py test_recurrence.py test_admin_settings.py test_maintenance.py
    │                     # test_drive_times.py test_first_run.py test_security_headers.py test_packaging.py test_app_messages.py
    └── common_tests/     # shared helpers (fake_ha.py, fake_ha_bus.py, env.py, ingress.py, packaging_core.py) and shared-module
                          # tests incl. test_app_bus.py (copies)
```

`app/common/`, `app/static/common/` and `tests/common_tests/` are copies of the repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see `common/README.md`). Never edit a copy: edit `common/` and re-sync (`tests/common_tests/test_shared_copies.py` fails if a copy was changed). `ha_notify.py` and `ha_people.py` live there now (identical in Family Tree, Arcade, Chat, Todo and Vault).

## 3. Manifest & options

**`config.yaml`**

| Group | Settings |
|---|---|
| App | `slug: household_todo`, `version: "2.3.2"`, arch amd64/aarch64/armv7/armhf/i386, `startup: application`, `boot: auto`, `url: https://github.com/sameerkotra/ha-apps` |
| Ingress | `ingress: true`, `ingress_port: 8100`, **no `ports:`** |
| Panel | `panel_icon: mdi:format-list-checks`, `panel_title: Household Todo`, `panel_admin: false` |
| Permissions | `homeassistant_api: true`, every other API/privilege false, `apparmor: true`; `map: share:rw` (maintenance files) |
| Environment | `DATA_DIR=/data`, `PORT=8100` |

The Dockerfile's CMD is `uvicorn app.main:app --host 0.0.0.0 --port 8100`.

**App option (the app's Configuration tab).** Only one:

| Option | Schema | Default | Meaning |
|---|---|---|---|
| `admin_users` | `[str]` | `[]` | HA login names or user ids, case-insensitive. The display name is never matched. Stays an option because it's how the first admin is known (you must be an admin to open App settings). |

- **Only `admin_users`.** `config.yaml` `options:` and `schema:` contain only `admin_users` (so does `translations/en.yaml`). Every other setting is an App setting (§3.1); any other key found in options.json is ignored (nothing is imported from it).
- **First run.** With `ADMIN_NAMES` empty nobody is an admin; `whoami.noAdmin` is true and every page shows the "No admin yet" banner (§9). Nobody is ever auto-promoted.
- **Loading options.** `config.py` reads `/data/options.json` (or `OPTIONS_PATH`) once, at import, for `admin_users` → `ADMIN_NAMES`. **Restart after changing it.**
- **Environment.** `SUPERVISOR_TOKEN` is injected by the Supervisor. `SUPERVISOR_CORE_API` defaults to `http://supervisor/core/api`. `DEV_ADMINS` (comma-separated extra admins) is used by the tests. `BACKGROUND_LOOPS=0` (set by the tests) stops the lifespan from starting the background loops (§8).
- **Constants.** `COMPLETED_RETENTION_DAYS = 60`. The time zone falls back to **UTC** when HA's `GET /config` → `time_zone` can't be read or applied.

### 3.1 App settings (`settings.py`, Admin → App settings)

| Key | Type / range | Default | Meaning (all apply live, `restartRequired: false`) |
|---|---|---|---|
| `expose_schedule_sensors` | bool | `true` | Publish a binary_sensor per schedule item (each item also has its own switch, §7.2). The settings route applies a change at once (remove all / full sync); the loop also re-checks every tick. |
| `sensor_refresh_minutes` | int 1–1440 | `5` | Full re-push interval, re-read every tick. |
| `drive_times_enabled` | bool | `false` | The **Drive times (uses OpenStreetMap services)** switch. Off: nothing is geocoded or routed (the warmer, home geocoding and Recalculate send nothing — Recalculate is a **409**), and every drive field in the API (`driveMinutes`, `driveAvoidTolls`, `driveTollsAvoided`) and every reminder drive note is blank; cached values stay in `places`. Turning it on or off resets the home location; on re-queues failed places and geocodes home in the background. **New install off; existing data on**: `_migrate()` runs `INSERT OR IGNORE INTO app_settings … SELECT 'drive_times_enabled','true' … WHERE EXISTS (home_address row that isn't blank) OR EXISTS (place with drive_minutes)`, then `INSERT OR IGNORE … 'false'`, so the decision is made once per database (a restored older backup gets it on import). |
| `home_address` | str ≤300, may be `""` | `""` | Where drive times are measured from (used only while `drive_times_enabled`). Blank leaves the feature unconfigured. A change resets the home location and every place's cached drive time and re-geocodes home (§7.3). |
| `osrm_url` / `nominatim_url` | `""` or an `http(s)://host…` URL ≤500 | `https://router.project-osrm.org` / `https://nominatim.openstreetmap.org` | Blank uses the default; a trailing `/` is stripped. Used on the next request; a change re-queues places whose estimate failed and retries a failed home lookup. |
| `avoid_tolls` | bool | `true` | Ask OSRM for `exclude=toll`, falling back to the fastest route. A change re-queues every place (via `drive_mode`). |
| `notify_place_details` | bool | `true` | **Place details in reminders** (Admin → App settings → Reminders). On: notifications for a task/item with a place add its address and phone (§8.1). Off: only the place name is sent (the earlier behaviour). |

- **Declared once.** `settings.py` lists every setting (`SETTINGS`: `Setting(key, default, label, help=, group=, min=, max=, show_if=, hidden=, …)`) and its `GROUPS` (Home Assistant sensors, Drive time, Reminders, Maintenance) on the shared `settings_core.Registry` (`app/common/settings_core.py`), which does validation, storage, the cache and the GET/PUT payload; the app keeps its hooks. `meta` describes each key (label, help, group, kind, range, `showIf`, …). The `maintenance_*` keys are `hidden` (edited on Admin → Maintenance) except `maintenance_files_path`, which is a field of App settings' **Maintenance** group with its status, **Check** / **Use this folder** and the confirmation.
- **Storage.** Table `app_settings(key PK, value JSON, updated_at, updated_by)` (§5). A key without a row uses `DEFAULTS`. `updated_by` is the admin's login name (or id).
- **Validation.** Pydantic model, `extra="forbid"`, `strict=True` (no `"5"` → 5, no `1` → true, no floats/Infinity/NaN for the int), strings stripped. Unknown key or bad value → **422** with a readable message naming the setting; nothing is written.
- **Reads.** `settings.get(key)` / `all()` go through an in-memory cache dropped on every write and whenever `db.generation()` changes (every `init_db()`, so a restore reloads). A load that overlapped a write (version counter) or a DB swap is returned but not cached. A stored value that no longer validates falls back to the default with a warning. Helpers: `drive_times_enabled()`, `osrm_url()`, `nominatim_url()`, `home_address()`, `drive_mode()`, `sensor_refresh_seconds()`.
- **`update(partial, user)`** merges, validates the whole result, writes only changed keys, returns `(values, changed)`.
- **No household digest time.** `reminder_time` is not a setting (an unknown key → 422): each person has their own `user_prefs.digest_time`, **08:00** until they pick one (`db.DEFAULT_DIGEST_TIME`). `_migrate()` fills every NULL `digest_time` with a stored household `reminder_time` value if an older database has one in `app_settings` (so nobody's time jumps), else `08:00`, then deletes the `reminder_time` and `_imported` rows and drops `user_notify_pending`. Idempotent.

## 4. Security, identity & admin

- **Ingress-only, enforced.** The `require_ha_ingress_auth` middleware (`auth_core.refuse_outsiders`) returns 403 unless `request.client.host` is in `auth_core.INGRESS_HOSTS` (`{172.30.32.2, 127.0.0.1, ::1}`, shared `app/common/auth_core.py`). That check is what makes the headers trustworthy, since sibling apps share the Docker network. `X-Ingress-Path` is not required. `/api/health` needs no user but is still IP-gated.
- **Security headers** (`web_security.SecurityHeaders(CSP, pragma=True).install(app)`, policy `CSP` in `main.py`): every response gets `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` (no inline scripts, handlers or style attributes) and `X-Content-Type-Options: nosniff`. A maintenance file sets its own policy (`sandbox`), which is kept.
- **Identity** (`get_current_user`).
  - `X-Remote-User-Id` is required; without it the request gets a 401 ("Open Household Todo from its panel…"). There is **no** dev-user fallback.
  - `username` comes from `X-Remote-User-Name`; the display name from `X-Remote-User-Display-Name`, else the login, else "Home Assistant User". Both are upserted on every request.
  - A user seen for the first time gets a row and a personal list called **"My Tasks"**.
- **Admin** means the lower-cased id or username is in `ADMIN_NAMES` (`auth_core.is_admin`; `auth.py` is a thin layer over `app/common/auth_core.py` with the same names and messages). The display name is never matched.
- **Admin area.** App settings, Users, Maintenance and Storage live behind one admin-only sidebar item. Every API behind them is `require_admin`: `/api/admin/settings`, `/api/admin/users…`, `/api/admin/notify-services`, `PATCH /api/users/{id}` and both storage routes. `GET /api/users` stays open to everyone (acting) because pickers need it, but returns only `{id, name, disabled}`.
- **Acting as** (`get_acting_user`, used by every data route).
  - Only an admin's `?as_user=<id>` counts; a non-admin's is ignored. An unknown id is a **404**, never a silent fallback.
  - The acting user keeps the real caller's `is_admin`.
  - Every write while acting logs `AUDIT <real> acting as <target>: <METHOD> <path>` at INFO.
- **Prefs** (`get_real_user_for_prefs`) always apply to the real caller. An admin whose `as_user` is someone else gets **409**.
- **`require_admin`** returns 403 and names `admin_users`. It guards everything in the Admin area (above) and `POST /api/schedule/sync`. It uses the real caller; `as_user` never matters.
- **Visibility.** User U sees a list or task if `l.kind='shared' OR l.owner_user_id = U`. Someone else's personal list is a **403** ("That list is private to someone else"); a missing one is a 404. Only an admin acting as the owner can see into it.
- **Private schedule items** (`visibility='private'`) exist only for their assignee: `schedule_logic.VISIBLE_SQL` = `(visibility = 'household' OR assigned_to = ?)` filters the schedule list, calendar, dashboard, reminders (assignee only) and every item/exception route, where an invisible item is a **404**. An admin sees one only while acting as the assignee. HA sensors publish it only if its `expose_sensor` is on (§7.2).
- **"U's task"** (used by reminders and workload) is a visible task that is assigned to U **or** sits in U's personal list.
- **Ownership.** Anyone may rename or delete a shared list; only the owner may for a personal one. Places, types and household schedule items (assigned or not) belong to the household: anyone can edit them. A private schedule item can be changed only by its assignee (`can_edit`; 403 guard, though visibility already makes it a 404 for everyone else).
- **Whoami** always reports the real caller, with counts only, never the lists. The UI doesn't show `viaIngress`, but the API returns it.

## 5. Data model

- **Connections.** Each operation opens a short-lived connection (`db_core.connect` / `db_core.transaction`, `app/common/db_core.py`) that sets `PRAGMA foreign_keys=ON` and registers `normalize_addr` (deterministic: collapses whitespace and lower-cases).
- **Formats.** IDs are `uuid4().hex`. Timestamps are UTC ISO to the second. Dates are `YYYY-MM-DD`; times are `HH:MM`.
- **`init_db()`** runs three steps in order:
  1. `SCHEMA` creates any missing tables and indexes.
  2. `_migrate()` first adds every missing column in `db.MIGRATIONS` (`db_core.add_missing_columns`), then fills `digest_time`, the drive-times rows and creates the address index. If existing rows are already duplicates, it only logs a warning.
  3. `_seed_first_run()`, only while `user_version < 1`: creates a shared **"Household"** list and the types Appointment, Doctor appointment, Errand, Chore and Bill (each only if none exist), then sets `user_version=1` so nothing is ever re-seeded.
  4. `app_bus.migrate()`: the app-messages tables `bus_outbox`, `bus_seen`, `bus_apps` (APP_MESSAGES_SPEC.md §4).

A fresh database and a migrated one end up identical:

```sql
PRAGMA journal_mode = WAL;
CREATE TABLE users (
  id TEXT PRIMARY KEY,                 -- HA user id
  name TEXT NOT NULL,                  -- display name, refreshed each request
  username TEXT,                       -- HA login (matched by admin_users)
  created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE lists (
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('personal','shared')),
  owner_user_id TEXT REFERENCES users(id),     -- personal only; NULL for shared
  created_at TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0);        -- order within shared / within one owner's personal lists
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
  title TEXT NOT NULL, notes TEXT,
  due_date TEXT, due_time TEXT,                -- time requires date
  priority TEXT CHECK (priority IN ('low','medium','high')),   -- NULL = none (no default)
  type_id TEXT REFERENCES task_types(id) ON DELETE SET NULL,
  place_id TEXT REFERENCES places(id) ON DELETE SET NULL,
  assigned_to TEXT REFERENCES users(id),
  completion_required INTEGER NOT NULL DEFAULT 0 CHECK (completion_required IN (0,1)),
  completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT, completed_by TEXT REFERENCES users(id),
  created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,         -- manual order within the list
  url TEXT,                                    -- migrated; http(s) link, NULL = none
  source TEXT);                                -- migrated; "Docs" when another app added it (§15)
CREATE INDEX idx_tasks_list ON tasks(list_id);          CREATE INDEX idx_tasks_due ON tasks(due_date);
CREATE INDEX idx_tasks_assigned ON tasks(assigned_to);  CREATE INDEX idx_tasks_completed ON tasks(completed, completed_at);
CREATE INDEX idx_tasks_list_pos ON tasks(list_id, position);
CREATE INDEX idx_tasks_type ON tasks(type_id);          CREATE INDEX idx_tasks_place ON tasks(place_id);
CREATE TABLE user_prefs (
  user_id TEXT PRIMARY KEY REFERENCES users(id),
  notifications_enabled INTEGER NOT NULL DEFAULT 0,   -- the daily digest switch (only the digest)
  lead_days INTEGER NOT NULL DEFAULT 0 CHECK (lead_days BETWEEN 0 AND 7),  -- digest look-ahead
  notify_on_assign INTEGER NOT NULL DEFAULT 1,
  digest_time TEXT);                                  -- migrated; 'HH:MM', each person's own; NULL (never set) = 08:00, filled by _migrate
CREATE TABLE notification_log (                       -- digest dedupe
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL CHECK (kind IN ('digest')), sent_on TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (user_id, kind, sent_on));
CREATE TABLE user_reminder_offsets (                  -- per-task "N minutes before due"
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  minutes_before INTEGER NOT NULL CHECK (minutes_before > 0 AND minutes_before <= 10080),
  created_at TEXT NOT NULL, UNIQUE (user_id, minutes_before));
CREATE INDEX idx_reminder_offsets_user ON user_reminder_offsets(user_id);
CREATE TABLE user_weekly_prefs (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  enabled INTEGER NOT NULL DEFAULT 0,
  day_of_week INTEGER NOT NULL DEFAULT 7 CHECK (day_of_week BETWEEN 1 AND 7),  -- ISO, 7 = Sunday
  time TEXT NOT NULL DEFAULT '18:00');
CREATE TABLE weekly_summary_log (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  sent_on TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE (user_id, sent_on));
CREATE TABLE task_reminder_log (                      -- keyed on due date/time: editing them re-arms
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id), minutes_before INTEGER NOT NULL,
  due_date TEXT NOT NULL, due_time TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (task_id, user_id, minutes_before, due_date, due_time));
CREATE INDEX idx_task_reminder_log_task ON task_reminder_log(task_id);
CREATE TABLE schedule_items (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT,
  rule TEXT NOT NULL, anchor_date TEXT NOT NULL,      -- §5.3
  lead_days INTEGER NOT NULL DEFAULT 1 CHECK (lead_days BETWEEN 0 AND 7),
  icon TEXT,                                          -- mdi:…; NULL = mdi:calendar-clock
  entity_slug TEXT NOT NULL UNIQUE,                   -- set at create, never changes
  created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL,
  -- migrated in this order; the defaults keep older items household, all-day, published
  assigned_to TEXT REFERENCES users(id),              -- NULL = household item (users are never deleted)
  start_time TEXT, end_time TEXT,                     -- 'HH:MM', both or neither; NULL = all-day
  place_id TEXT REFERENCES places(id) ON DELETE SET NULL,
  visibility TEXT NOT NULL DEFAULT 'household' CHECK (visibility IN ('household','private')),
  expose_sensor INTEGER NOT NULL DEFAULT 1,           -- per-item HA sensor switch
  url TEXT);                                          -- migrated; http(s) link, NULL = none
CREATE TABLE schedule_exceptions (
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES schedule_items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('skip','move','add')),
  date TEXT NOT NULL, to_date TEXT, reason TEXT,
  created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL,
  CHECK ((kind = 'move') = (to_date IS NOT NULL)));
CREATE UNIQUE INDEX idx_sched_exc_orig ON schedule_exceptions(item_id, date) WHERE kind IN ('skip','move');
CREATE UNIQUE INDEX idx_sched_exc_add  ON schedule_exceptions(item_id, date) WHERE kind = 'add';
CREATE TABLE schedule_reminder_log (                  -- "N before" dedupe per occurrence; moving/retiming re-arms
  id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES schedule_items(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id), minutes_before INTEGER NOT NULL,
  date TEXT NOT NULL, start_time TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (item_id, user_id, minutes_before, date, start_time));
CREATE INDEX idx_sched_reminder_log_item ON schedule_reminder_log(item_id);
CREATE TABLE task_types (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  icon TEXT, color TEXT,                              -- emoji (≤8 chars), #RRGGBB
  position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE places (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL,
  phone TEXT,                                         -- migrated
  lat REAL, lon REAL,                                 -- migrated; geocoded address
  drive_minutes INTEGER,                              -- migrated; NULL = unknown/failed
  drive_checked_at TEXT,                              -- migrated; last attempt; NULL = queue now
  drive_mode TEXT,                                    -- migrated; 'no_tolls' | 'fastest' the cache used
  drive_tolls_avoided INTEGER,                        -- migrated; 1 = toll-free route really used
  created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL);
CREATE UNIQUE INDEX idx_places_address_norm ON places (normalize_addr(address));  -- created in _migrate
CREATE TABLE task_items (                             -- checklist
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX idx_task_items_task ON task_items(task_id, position);
CREATE TABLE app_settings (                           -- §3.1
  key TEXT PRIMARY KEY, value TEXT NOT NULL,          -- JSON
  updated_at TEXT NOT NULL, updated_by TEXT);         -- admin login/id
CREATE TABLE user_notify (                            -- extra notify services per person (Admin → Users)
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL,                              -- 'notify.<name>', name ^[a-z0-9_]+$
  created_at TEXT NOT NULL, created_by TEXT,          -- admin login/id
  PRIMARY KEY (user_id, service));
-- PRAGMA user_version = 1 after the first-run seed. REQUIRED_TABLES (restore check) = the 14 oldest tables:
-- schedule_reminder_log, app_settings, user_notify and the maint_* tables are left out so older backups still restore (init_db creates them right after).
```

### 5.1 Tasks

- **Field limits** (422 unless noted): title 1–200 chars, trimmed; notes ≤5000 (blank → NULL); `url` per the **link rule** below; `due_time` `HH:MM` and needs a date (clearing the date clears the time); priority low/medium/high/null; `type_id` must exist; `place_id` must exist, else **404**; `completion_required` bool; `items` ≤100 strings of 1–200 chars.
- **Link rule** (`links.clean_url`, shared by tasks and schedule items). Optional; null, blank or whitespace → NULL; otherwise trimmed, ≤2000 chars, no whitespace or control characters, and an absolute `http://` or `https://` URL with a host by `urllib.parse.urlsplit` (the scheme is stored lower-cased). Anything else — `javascript:`, `data:`, `ftp:`, relative — is a 422 naming the rule. PATCH `url: null` (or blank) clears it.
- **Assignment.** An unknown user is a 422. These are 400, checked only when the assignee *changes*: assigning a personal-list task to anyone but the list's owner, or to a disabled user. A later disable leaves existing assignments alone.
- **Completion.** `completed:true` stamps `completed_at`/`completed_by` (the acting user); `false` clears both. The checklist and completion never affect each other.
- **Overdue** means open, `completion_required=1` and `due_date < today`, judged by date only. An optional task past its date is only **past**: shown muted, never counted, never in the digest.
- **Position.** New tasks, lists and items get `MAX+1`. Reorder sets `position=index` only for the ids sent; other rows keep their position.
- **Sorts** (ties → position, then `created_at`): `manual` = position; `due` = undated last, all-day before timed; `priority` = high → low, none last; `assignee` = A → Z, unassigned last; `created` / `completed` = newest first.
- **Fixed orders.** A calendar day: all-day, then time, priority, position. The dashboard: date, all-day, time, priority, position.
- **Task JSON:** `id, listId, listName, listKind, title, notes, url, dueDate, dueTime, priority, typeId, type{id,name,icon,color}|null, placeId, place{id,name,address,phone,driveMinutes,driveTollsAvoided}|null, assignedTo, assigneeName, completionRequired, completed, completedAt, completedBy, completedByName, createdBy, createdByName, createdAt, position, items[{id,text,done}], itemsDone, itemsTotal, overdue, past, source` (`source`: e.g. "Docs" — shown as a "from Docs" chip — or null).

### 5.2 Places, types, lists

- **Places.** Name 1–60 chars; address 1–300 chars (newlines allowed); phone optional, up to 30 chars, free-form.
  - A duplicate normalized address is a **409** that names the existing place. The app pre-checks, and the index is the backstop (an `IntegrityError` is re-checked into the 409).
  - Changing an address clears `lat`, `lon`, `drive_minutes`, `drive_checked_at`, `drive_mode` and `drive_tolls_avoided`.
- **Types.** Name 1–30 chars, unique ignoring case (409 on a duplicate); icon up to 8 chars; colour `#RRGGBB`. Ordered by position, then name. There is no reorder route.
- **Deleting** a place or type sets its task references to NULL. `usageCount` counts only visible tasks.
- **Lists.** Name 1–60 chars; `kind` can't change after creation.
  - `GET` returns shared lists first, then the acting user's personal lists, each group ordered by position then `created_at`, with `openCount`.
  - Deleting a list cascades to its tasks.
  - Quick add re-creates "My Tasks" if the user has no personal list.

### 5.3 Recurrence (`recurrence.py`, pure)

D is an occurrence iff `D >= anchor_date` and D matches the rule. Weekdays are ISO (1 = Mon … 7 = Sun).

| Rule | Range | D matches when |
|---|---|---|
| `daily` | — | always |
| `weeks:N:d,d,…` | N 1–52; days strictly ascending | Its weekday is listed **and** the whole weeks between the anchor's Monday and D's Monday are a multiple of N. |
| `every:N` | N 1–999 | (D − anchor) is a multiple of N. |
| `monthly:day` | day 1–31 | D.day == min(day, last day of the month). |
| `months:N:day` | N 2–36 | Months since the anchor's month are a multiple of N, with the same clamp. |
| `years:N` | N 1–10 | Years since the anchor are a multiple of N, same month, and D.day == min(anchor.day, last day). A 29 Feb anchor falls on 28 Feb in non-leap years. |
| `nth:n:d` | n ∈ {1,2,3,4,−1} | The weekday is d and `(day−1)//7+1 == n`; for −1, `day+7 > last day`. |

- **Validation** (`RULE_RE`). The regex must match, `weeks` days must ascend, and the anchor must be a valid date. A `weeks` anchor must also fall on one of the listed days. Each failure is a 422 with its own message.
- **Anchor.** For `weeks`, `every`, `months` and `years` the anchor sets the phase; the UI labels it "First occurrence". For the other rules it is just "Starting from".
- **Labels** (`describe_rule`). "Daily", "Every Monday", "Every Mon & Thu", "Every 2 weeks on Mon", "Every day" / "Every N days", "Monthly on the 15th", "Every 3 months on the 15th", "Every year" / "Every N years", "2nd Tuesday of the month", "Last Friday of the month".
- **`min_gap_days`** (drives the UI's "always on" warning): daily 1; `every:N` N; `weeks` the smallest gap between listed days, including the wrap `7N−(last−first)`; `months:N` 28N; `years:N` 365N; `monthly`/`nth` 28.
- **Stepping.** Occurrences are found one day at a time, bounded at 3,700 days (×51 when exceptions are involved).

### 5.4 Schedule items & exceptions (`schedule_logic.py`)

- **Item fields.** Name 1–60 chars; notes up to 500; `lead_days` 0–7 (1 when omitted or null on create); icon must match `^mdi:[a-z0-9-]+$`; `url` per the link rule (§5.1).
- **Person fields**, validated against the *resulting* item, so PATCH stays partial:
  - `assigned_to` like tasks: an unknown user is a 422; a disabled one is a 400 only when the assignee changes. NULL = household item.
  - `start_time`/`end_time`: `HH:MM` (`TIME_RE`), both or neither, and end > start on the same day — each a 422. Clearing both makes the item all-day again. The times apply to every occurrence; a move keeps them.
  - `place_id` must exist (404); deleting the place sets it NULL.
  - `visibility` household|private (422); private needs an assignee (422), so unassigning a private item fails until it's made household. **Only the assignee** can create or keep an item private: the resulting `assigned_to` must equal the acting user (422), so nobody can hide someone else's item from the household.
  - `expose_sensor` bool (422). Defaults on create: 0 for a private item, else 1.
- **Occurrences** are never completed or overdue and never count in workload cards; a cancelled session is a skip.
- **Limits.** 100 items, and 50 exceptions per item; beyond either is a 422.
- **Slug.** NFKD → ASCII, lower-cased, runs of other characters → `_`, trimmed to 40. Empty becomes `item`; collisions get `_2`, `_3` and so on. It never changes on rename.
- **Effective dates.**
  - `removed` = dates of skips and moves; `added` = move `to_date`s plus add dates.
  - D is effective iff `(occurs_on ∧ ∉removed) ∨ ∈added`.
  - `next_on_or_after` returns the earlier of the next occurrence not in `removed` and the earliest `added` date that is today or later. Exceptions never shift the phase.
- **State.** `nextDate`, `daysUntil`, `occursToday` (date-based; today counts all day) and `sensorOn`:
  - all-day item: `daysUntil <= lead_days`, flipping at local midnight;
  - timed item: `daysUntil == 0 and start <= now < end` in HA's zone; `lead_days` is ignored.
  - `nextStart`/`nextEnd`: ISO datetimes (with offset) of the next window that hasn't ended; null for all-day items.
- **Exception validation.** Every failure is a 422.
  - `kind` must be skip, move or add.
  - Every date must be between today and today + 366. `reason` is at most 60 chars.
  - A move needs a `to_date` different from its `date`; only moves may have a `to_date`.
  - A skip or move `date` must be a rule occurrence.
  - A move target or add date must not already be effective, and must not be the original date of an existing skip or move ("undo that first"). A move target also must not be a rule occurrence.
  - The 50-exception cap counts the new one.
- **Replacing.** A new skip or move **replaces** any existing one for the same original date. Re-posting an identical exception is idempotent and skips validation.
- **Rule changes.** Changing the rule or anchor (only when the value actually changes) deletes the item's skips and moves; adds are kept.
- **`upcoming`.** The next 6 displayed dates: rule occurrences (normal / skipped / moved_away) merged with added and moved_here dates. Each has `exceptionId`, `reason`, `by` (a name) and `movedTo` / `movedFrom`. Calendar entries use the same statuses for a date range.
- **Item JSON.** `id`, `name`, `notes`, `url`, `rule`, `ruleLabel`, `anchorDate`, `leadDays`, `icon`, `entitySlug`, `entityId`, `minGapDays`, `createdBy` (a name), `createdAt`, `assignedTo`, `assigneeName`, `startTime`, `endTime`, `placeId`, `place{id,name,address,driveMinutes}|null`, `visibility`, `exposeSensor`, `published` (global option AND `exposeSensor`), plus the state fields and `upcoming`.

## 6. API

**Auth levels.**
- **acting**: `get_acting_user`.
- **real**: the real caller; a 409 while acting as someone else.
- **admin**: `require_admin`.
- **+priv**: someone else's personal list returns 403.

Every route except health needs the user headers. Errors come back as `{"detail": …}`. Creates return 201 and deletes 204, unless noted otherwise.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/health` | IP only | `{"status":"ok"}` |
| GET | `/api/whoami` | acting (reports real) | The shared contract (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`) `{haUserId, haUsername, haDisplayName, nameSent, isAdmin, displayNameOnly, adminEntries, noAdmin, viaIngress, notifyLinked, extras:[Reminder service linked]}` plus the app's own `actingAs:{id,name}\|null`, `notifyEntries`, `maintenance:{enabled}`, `driveTimes:{enabled}` — `noAdmin` is true while `admin_users` is empty |
| GET | `/api/users` | acting | Household members for pickers — everyone, including disabled users, sorted by name: `{id,name,disabled}` (no `createdAt`; that's in the admin list) |
| PATCH | `/api/users/{id}` | admin | `{disabled: bool}`. 404 if the user is unknown. Only the UI stops you disabling yourself. |
| GET | `/api/admin/users` | admin | `{users:[{id,name,username,disabled,createdAt,notify:["notify.x",…],ha}]}` (`ha` from `people_admin.ha_person_json`, §7.4) |
| GET | `/api/admin/notify-services` | admin | `refresh=1` skips the cache. `{available:true, error:null, services:["notify.…"], entities:["notify.…"]}`, sorted; if HA can't be asked: **200** `{available:false, error:"Couldn't read the notify services from Home Assistant: …", services:[], entities:[]}` (keeps the page usable and the console clean). |
| PUT | `/api/admin/users/{id}/notify` | admin | `{services:[…]}` replaces the set (deduplicated, ≤10). Each is `notify.` + `[a-z0-9_]+` (a bare name gets the prefix); else 422. Returns the admin user JSON. 404 unknown user. |
| POST | `/api/admin/users/{id}/notify` | admin | `{service}` adds one (201; already there = unchanged). 422 invalid or over 10. |
| DELETE | `/api/admin/users/{id}/notify/{service}` | admin | Removes one; 404 if they don't have it. Returns the admin user JSON. |
| POST | `/api/admin/users/{id}/notify/test` | admin | Optional `{service}` (must be theirs, else 422). Sends "Test notification from Household Todo for <name>, sent by an admin." to every (or that) service via `send_notify`, ignoring the person's own switches. 400 if they have none; 429 within 10 s per person; **502** with HA's hint if none was accepted; else `{results:{"notify.x":bool}}`. DB connection closed before HA is called. |
| GET | `/api/admin/connected-apps` | admin | `{on, connected, apps: [{slug, name, version, can, last_seen, active}]}` — the read-only Connected apps card under App settings (§15). |
| GET / PUT | `/api/admin/settings` | admin | §3.1. GET → `{values, defaults, meta:{key:{label, help, group, kind, restartRequired:false, min?, max?, showIf?, hidden?, maxLength?, …}}, groups:[{id, label, help}], maintenanceFiles}`. PUT takes any subset; 422 on an unknown key or bad value; returns the same shape. Side effects in §3.1 (sensor add/remove and home re-geocode run as background tasks). |
| GET / POST | `/api/lists` | acting | POST takes `{name, kind}`. A personal list is owned by the acting user. |
| POST | `/api/lists/reorder` | acting | `{kind, ordered_ids}`. Declared before `/lists/{id}`. 400 on a duplicate id, the wrong kind, or someone else's list. |
| PATCH / DELETE | `/api/lists/{id}` | acting (403 on someone else's personal list) | PATCH takes `{name}` only. DELETE cascades. |
| GET | `/api/lists/{id}/tasks` | acting +priv | `show=open\|completed`. `sort` defaults to `manual` for open and `completed` for completed; `completed`+open or `manual`+completed is a 422. Filters: `assignee` (`me`, `unassigned` or an id), `priority` (…/`none`), `type` (an id or `none`), `place`, `completion`. |
| POST | `/api/lists/{id}/tasks` | acting +priv | Task body. May queue an assignment ping. |
| POST | `/api/tasks` | acting | Quick add. Optional `list_id` defaults to the first personal list. |
| PATCH / DELETE | `/api/tasks/{id}` | acting +priv | Any field; `null` clears it. `completed: bool`. `list_id` moves the task to another list the acting user may add to (a shared list or their own personal one; 403 otherwise, 404 if unknown, 422 if not a string). It goes to the end of that list and keeps its checklist, dates and other fields. Moving into a personal list unassigns anyone but the owner. |
| POST | `/api/lists/{id}/tasks/reorder` | acting +priv | `{ordered_ids}`. 400 if an id isn't in the list, or appears twice. |
| POST | `/api/tasks/{id}/items` | acting (via task) | `{text}` or `{texts:[…]}`; blank entries are skipped. Returns the task JSON. |
| PATCH / DELETE | `/api/task-items/{id}` | acting (via task) | `{text?, done?}`. Returns the task JSON. |
| GET / POST / PATCH / DELETE | `/api/task-types[/{id}]` | acting | `{name, icon?, color?}` plus `usageCount`. A duplicate name is a 409. |
| GET | `/api/places?q=` | acting | Sorted by name; `q` is a case-insensitive substring of the name or address. Returns `{id,name,address,phone,driveMinutes,driveAvoidTolls,driveTollsAvoided,usageCount}`; the drive fields are null when there's no estimate. |
| POST / PATCH / DELETE | `/api/places[/{id}]` | acting | A duplicate address is a 409. Changing the address resets its drive time. Anyone can delete. |
| POST | `/api/places/{id}/recalculate-drive-time` | acting | See §7.3. |
| GET | `/api/schedule` | acting | Visible items with their state and `upcoming`, sorted by `nextDate`, all-day first, start time, then name. `assignee=me\|unassigned\|anyone\|<id>` (default anyone). |
| POST / PATCH | `/api/schedule[/{id}]` | acting (private: assignee only) | `{name, rule, anchor_date, lead_days?, icon?, notes?, url?, assigned_to?, start_time?, end_time?, place_id?, visibility?, expose_sensor?}`. PATCH is partial; `rule`+`anchor_date` and the two times are validated together. Pushes the sensor in the background, or deletes the entity when `expose_sensor` was just turned off. May queue an assignment ping. |
| DELETE | `/api/schedule/{id}` | acting (private: assignee only) | Cascades to exceptions and the reminder log. Deletes the HA entity in the background. |
| POST | `/api/schedule/sync` | admin | Synchronous full sync. Returns `{pushed, failed}`. |
| POST | `/api/schedule/{id}/exceptions` | acting (private: assignee only) | `{kind, date, to_date?, reason?}`. Returns the item (201) and re-pushes the sensor. |
| DELETE | `/api/schedule/{id}/exceptions/{eid}` | acting (private: assignee only) | Returns the item (**200**), or 404. Re-pushes the sensor. |
| GET | `/api/calendar` | acting | `from`, `to` (inclusive, `to−from` ≤ 92 days), `type`, `assignee` (`me`, `unassigned`, `anyone` or an id), `show=open\|all`, `schedule=0\|1`. Returns `{today, tasks, schedule, undatedCount}`. |
| GET | `/api/dashboard` | acting | Visible open tasks split into `overdue` (required only), `dueToday` and `upcoming` (tomorrow to +7). Also `counts{…,open}` and `schedule` ("Coming up"): visible items whose sensor is on, plus the acting user's own items occurring today or tomorrow, as `{itemId,name,icon,nextDate,daysUntil,occursToday,startTime,endTime,sensorOn,private}`. |
| GET | `/api/workload` | acting | `people[{userId,name,disabled,isMe,open,overdue,dueSoon,doneThisWeek}]` (you first, then by name) and `unassigned{open,overdue,dueSoon}`. |
| GET | `/api/prefs` | real | `{notificationsEnabled, leadDays, notifyOnAssign, digestTime (stored; 08:00 if never set), reminderOffsets (desc), weeklySummary{enabled,dayOfWeek,time}, canNotify, canNotifyReason}` |
| PUT | `/api/prefs` | real | Partial. `digestTime` is `HH:MM`; **null resets it to 08:00** (there's no household default to fall back to). Every save stores a concrete time. `reminderOffsets` replaces the list: up to 5 integers in 1–10080, deduplicated. `weeklySummary` is also partial. Enabling while `canNotify` is false is a 400. |
| POST | `/api/prefs/test-notification` | real | 400 if the user can't be notified; 429 within 10 s; 502 with a hint if HA refuses. |
| GET | `/api/admin-storage-download-db` | admin | Online backup to a temp file, sent as `household-todo-backup-YYYYmmdd-HHMMSS.db`, then deleted. |
| POST | `/api/admin-storage-import-db` | admin | Multipart `file`. 400 with a specific message; returns `{status:"ok"}`. See §10. |

- **Calendar.** `tasks` are the visible dated tasks, sorted per day. `schedule` holds the visible items' entries, each with `startTime`, `endTime`, `assignedTo`, `assigneeName`, `private` and `url`, sorted per day all-day first, then by start time. The `assignee` filter applies to them too (household items count as unassigned). `undatedCount` counts visible open tasks with no due date.
- **Workload.** Counts use visible open tasks.
  - `overdue`: required tasks with a due date before today.
  - `dueSoon`: due from today to today + 7.
  - `doneThisWeek`: tasks with `completed_by` = that person and `completed_at` in the last 7 days.
- **Whose tasks each card counts.** Your own card counts all your tasks. Other people's cards count only shared-list tasks assigned to them. "Unassigned" counts shared-list tasks with no assignee. A disabled user with nothing open is left out.
- **`canNotify`** is false when the user has no assigned notify service (`user_notify`), or there's no Supervisor token. The reason says to ask an admin (Admin → Users; no restart).
- **Test notification** (`/api/prefs/test-notification`) goes to every assigned service; 502 only if none accepted it, otherwise `{status:"sent", failed:[…]}`.
- **Whoami** `notifyEntries` is the number of people with at least one service ; `notifyLinked` = the caller has one.

## 7. Home Assistant & external integrations

- **Calling HA.** Every call goes to `SUPERVISOR_CORE_API` with `Bearer SUPERVISOR_TOKEN`. `ha_client.request()`, `post_state` and `delete_state` come from the shared `app/common/ha_client.py`; the app's `ha_client.py` re-exports them and adds `load_timezone`. `ha_client.request()` never raises. A missing token logs one warning per process.
- **Time zone.** Read once at startup from `GET /config` (`app/common/ha_time.py`; `config.today()` / `now()` are backed by `config.ZONE`), falling back to UTC. The same call reads `latitude` (maintenance seasons) and `currency`.

### 7.1 Notifications (`app/common/ha_notify.py`)

**`send_notify(service, title, message, data=None) -> bool`** never raises. It proceeds as follows:
1. Refuse any name that doesn't match `^[a-z0-9_]+$`.
2. `POST /services/notify/<name>` with `{title, message}`, plus `data` when given.
3. On 400 or 404, if the name is a known **notify entity** (or the targets can't be read), `POST /services/notify/send_message` with `{entity_id:"notify.<name>", title, message}` — never `data`, which that action doesn't accept. On success, remember the name in `_entity_mode` (later sends go straight there, also without `data`).
4. On final failure, log HA's message, plus a hint for 400 or 404.

- **Target discovery** (`fetch_notify_targets_blocking`, cached **60 s**, successes only). Actions come from `GET /services` (`domain == "notify"`, names matching `^[a-z0-9_]+$`, minus `send_message`). Entities are the `notify.*` entries in `GET /states` (best effort). Raises `NotifyListError` with a readable reason (no token / no answer / HTTP status / unreadable). `available_notify_targets()` returns None instead; `list_notify_services_blocking()` formats it for Admin → Users. Never called while a DB connection is open. `persistent_notification` is a valid target but is never suggested in hints.
- **Failure hints.** `explain_failure` suggests close names ("did you mean notify.…?") or lists the phone targets. It also says where to look and to fix it under Admin → Users.
- **Startup check** (`check_targets_blocking`, best effort). Reads every assigned service from the DB, closes it, then asks HA. Logs INFO for each that is an entity, and marks it. Logs WARNING with a hint for each that matches nothing.
- **Assignments.** `user_notify` is keyed by the HA **user id** (stable, not user-editable). `assigned_services(conn, user)` = the user's `user_notify` rows, by id only — never a login or display name. `services_for(user, conn=None)` returns bare names for `send_notify` (an empty list when nobody's assigned). The module is shared (`common/python/ha_notify.py`) with Family Tree, Household Arcade, Household Vault and Household Chat. `services_for` = the person's HA phones (§7.4) + their `user_notify` extras, without duplicates; `ha_notify.ha_phones` imports `ha_people` only if it exists. `normalize_service` accepts `notify.x` or `x`, requires `^notify\.[a-z0-9_]+$`.
- **Content.** Only titles (or item names), times, place names and links are sent; notes and addresses never are. A schedule item is only ever named to its assignee. The notification title is always "Household Todo".

### 7.2 Schedule sensors (`ha_sensors.py`)

- **Publishing.** `POST /states/binary_sensor.household_todo_<slug>` with `{state: on|off, attributes}`, only while `expose_schedule_sensors` **and** the item's `expose_sensor` are on. A private item defaults to off because any HA user can read sensors.
- **State.** §5.4: all-day items are on from `lead_days` before a date to its end; timed items only inside `[start, end)` on each effective date (skips removed, moves and extra dates included, times kept).
- **Attributes.** `friendly_name`, `icon` (default `mdi:calendar-clock`), `next_date`, `days_until`, `occurs_today`, `lead_days`, `rule` (the label), `skipped_dates` / `extra_dates` (upcoming only, up to 10 each), `assigned_to` (display name or null), `start_time`, `end_time`, `next_start`, `next_end` (null for all-day items).
- **Deleting.** `DELETE /states/<id>`; a 404 counts as success. Full syncs also DELETE each unpublished item's entity once per process (`_cleared_unpublished`), so a failed delete after turning "Publish" off doesn't leave a stale sensor.
- **Re-pushing.** HA doesn't persist these entities, so the loop keeps re-pushing them (§8).
- **Immediate pushes.** Creating, editing, and adding or undoing an exception push immediately as a background task. Deleting an item, or turning its `expose_sensor` off, deletes its entity; full syncs skip it. A restore triggers a full sync.
- **Shared publisher.** The "last pushed state" memory is `ha_sensors.SENSORS`, a `sensor_publisher.Publisher` (`app/common/sensor_publisher.py`, state-only key); the loop's full-sync and maintenance timings are two `sensor_publisher.Refresh` (every `sensor_refresh_minutes`, and on a date change), and the loop itself is `sensor_publisher.run`.
- **Timed windows.** The module keeps the last state pushed per entity in memory. On loop ticks between full syncs it recomputes every published timed item and pushes those whose state changed, so a window opens and closes within ~60 s. A failed push isn't recorded and is retried next tick; a restart simply re-pushes everything.
- **Failures.** A full sync aborts after 3 consecutive failures, logging one warning per streak. A push never fails a request.
- **`expose_schedule_sensors` off** (App setting, live). Pushes and deletes become no-ops and `published` is false. The loop checks it every tick: on the first tick with it off (startup, or just switched off) it deletes every known entity and clears its memory of pushed states; when it comes back on, the next tick does a full sync. The settings route also calls `apply_exposure_change_blocking()` straight away (remove all / full sync). A restore does the same.

### 7.3 Drive time (`geocode.py`, `drive_time.py`)

- **Enabling.** The feature needs the `drive_times_enabled` switch on (§3.1) and a geocoded home (`HOME_LATLON`); with the switch off `ensure_home_blocking()` treats the address as blank (no lookup), `compute_for_address()` returns all None without calling anything, and `feature_enabled()` is false. `osrm_url` always resolves (blank = default). `ensure_home_blocking()` geocodes the current `home_address` at startup, at the start of every drive-time tick if it isn't done, and in the background after the setting changes; a failure logs one warning per address and is retried at most hourly (sooner after a server-URL change via `retry_home_soon()`). `reset_home()` (settings route, restore) drops it at once and bumps `home_generation()`. The lock is never held during the lookup. While it's off, nothing is shown, sent or raised.
- **Geocoding (Nominatim).** `GET {nominatim_url}/search?q=…&format=json&limit=1`.
  - UA `HouseholdTodo-HomeAssistantAddon/1.0 (…)`; throttled process-wide to one call per 1.05 s (`geo.Throttle`); 10 s timeout. The throttle, the Nominatim search URL / first hit, the OSRM coordinates and the HTTP fetch are the shared `app/common/geo.py`; the app keeps its User-Agent, queries, retries, cache and errors.
  - **If there are no results**, it strips one suite/unit designator (`#150`, Suite, Ste., Unit, Apt, Apartment, Bldg, Building, Fl, Floor, Rm, Room, plus the token after it), tidies spaces and commas, and retries once. If there's nothing to strip, it doesn't retry.
- **Routing (OSRM).** `GET {osrm_url}/route/v1/driving/{lon},{lat};{lon},{lat}?overview=false`. Coordinates are **lon,lat**.
  - The response must have `code=="Ok"`. Minutes = `max(1, round(duration/60))`.
  - With `avoid_tolls`, the request first adds `&exclude=toll`, returning `(min, True)`.
  - On any failure, including `NoRoute` or `InvalidValue` (both HTTP 400), it falls back to the plain route, returning `(min, False)`.
- **Cache.** `compute_for_address()` returns `(lat, lon, minutes, tolls_avoided)`; any of them may be None. Every write stamps `drive_checked_at` and `drive_mode` (`no_tolls` or `fastest`).
- **Recalculate.**
  1. Return 404 if the place is unknown. This check comes **first**.
  1a. Return **409** ("Drive times are turned off…") while `drive_times_enabled` is off.
  2. If a home address is set but not geocoded yet, try once now (no DB connection open). Return 400 if the feature is still off; the message points to Admin → App settings.
  3. Look up the address **with no DB connection open**, ignoring cooldowns.
  4. Reload the place (404 if it was deleted meanwhile), update it and commit.
  5. Return 502 if there are no minutes; otherwise return the place JSON.
- **Leave-by** = due time (a schedule item's start time) − `drive_minutes`.
  - In the UI it appears only when there's both a place with an estimate and a due time. It wraps past midnight.
  - Notifications append ` · ~N min drive, leave by HH:MM`.

### 7.4 People and phones (`app/common/ha_people.py`)

Shared (`common/python/ha_people.py`) with Family Tree, Arcade, Chat and Vault; the Admin → Users routes use `app/common/people_admin.py` (`ha_person_json`, `refresh_people`, `notify_services`, `clean_service`, `add_service`, `remove_service`, `TestLimiter`, `test_results`, `require_one_sent`).

- **Reading the people:** one `POST /api/template` renders every `person.*` with its linked `user_id`, state and picture, and each tracked device that belongs to the `mobile_app` integration (`device_id` / `device_attr`; the device registry isn't on REST). A device's notify action is `mobile_app_<slugify(device name)>`, or HA's `_2…` numbered one, checked against `GET /api/services`; a device with none is shown but not used.
- **Refreshing:** at start-up, every 5 minutes (a background loop) and on *Check Home Assistant again* (`GET /api/admin/users?refresh=1`). If HA can't be read, the last answer is kept; everything else only reads the cache, so it never calls HA while holding a DB connection.
- **Admin → Users:** *Phones* (from HA, read-only; `ha {known, person, personName, phones[{label, service, tracker}]}` in the admin user JSON), *Also* (the `user_notify` extras), and *Send a test* to both. People still appear on first open and can be disabled here.

## 8. Background jobs

The lifespan starts the background loops (unless `BACKGROUND_LOOPS=0`) through the shared Jobs runner (`app/common/housekeeping.py`), in this order: `reminders`, `ha_sensors`, `housekeeping`, `drive_time`, `ha_people` (§7.4), `maint_files` (§14.5). Each one runs in the threadpool, catches and logs its own errors every tick, and is cancelled on shutdown; shutdown waits for a pass already running in a worker thread. `housekeeping.loop` is built with `housekeeping.periodic()`. Logging is set up by `housekeeping.setup_logging()`.

| Loop | Tick | Work |
|---|---|---|
| `reminders` | 60 s | Digest, weekly, task-reminder and schedule-reminder passes, each isolated from the others |
| `ha_sensors` | 60 s | Re-reads both settings every tick. Off → remove all once. On → full sync on the first tick, on a local date change, after being switched back on, and every `sensor_refresh_minutes`; otherwise pushes timed items whose state changed |
| `housekeeping` | 60 s | On the first tick and on a local date change |
| `drive_time` | 60 s | `ensure_home_blocking()`, then at most **one place per tick**, only while the feature is on. Lookups run with no DB connection open; a result is dropped if home or `avoid_tolls` changed meanwhile (`home_generation`, `drive_mode`), and written only if the address is unchanged |

### 8.1 Reminders (`reminders.py`)

- **All kinds.** A send needs a token and a user with `disabled=0`, `notifications_enabled=1` and at least one assigned service. Each notification goes to **every** assigned service; it counts as sent (log row written) if at least one accepted it. Only that user's own tasks count ("U's task", §4). A successful send writes a log row. A failed send doesn't, so it is retried on the next tick.
- **Digest.** Sent within the **60 minutes after** the person's own `digest_time` (08:00 if never set), at most once per local day (`notification_log`).
  - Contents: open tasks due from today to today + `lead_days`, plus overdue *required* tasks. Overdue items come first, then by date, time and position.
  - Up to 5 lines of `• Title — when[ @ Place][ drive note]`, then `+K more`. "when" is "N days overdue", "today HH:MM", "tomorrow" or "in N days". An empty digest isn't sent.
- **Weekly summary.** Needs `enabled`. Sent on its ISO weekday, within 60 minutes of its time, and deduplicated by `weekly_summary_log`. It lists tasks due from tomorrow to today + 7, in up to 10 lines, and is never sent empty.
- **Per-task reminders.** Only tasks with a **due time**, due from today − 1 to today + 10.
  - A reminder fires once `now ≥ due − offset`, and is skipped once `now ≥ due`.
  - Message: `Title — due in N hours/minutes/days (HH:MM)[ @ Place][ drive note]`.
  - Deduplicated on (task, user, offset, due_date, due_time), so editing the due date or time re-arms it.
- **Schedule items.** Every item with `assigned_to` (household or private) counts for its assignee only; unassigned household items never notify. Same gates as tasks.
  - Digest and weekly summary mix in the item's effective dates in their window, sorted with the tasks by date then time: `• Yoga — today 18:00–19:00 @ Studio[ drive note]`; an all-day item shows no time.
  - "N before" (own pass): the assignee's offsets against each occurrence's start (dates today…+8): `Yoga — starts in 1 hour (18:00–19:00) @ Studio[ drive note]`, same fire/skip rule as tasks, deduplicated in `schedule_reminder_log` on (item, user, offset, date, start_time).
- **Assignment ping.** Queued as a background task when the assignee changed, is set, and isn't the acting user. The assignee needs notifications on, `notify_on_assign` on, and an assigned service. Message: `<acting name> assigned you: <title>[ (due YYYY-MM-DD)]`, or for a schedule item `… assigned you: Yoga (Every Tue, Thu & Fri, 18:00–19:00)`.
- **Links.** A single-item notification — task "N before", schedule "N before", assignment ping — for a task/item with a `url` gets `\n<url>` appended and is sent with `data={"url": <url>, "clickAction": <url>}` (iOS Companion opens `url`, Android `clickAction`), which `send_notify` passes on the action path only. Without a URL the sender is called exactly as before (no `data`). Digest and weekly summary add `   🔗 <url>` after each *shown* line that has one; the `+K more` count is unchanged. The test notification never has a link.
- **Place details** (`notify_place_details`, default on). `place_details(place)` returns `{address, phone}` when the setting is on and the place has either, else None. A single-item notification (task "N before", schedule "N before", assignment ping) appends `📍 <address>` and `📞 <phone>` lines after the message and before the link; `data["actions"]` gets `{"action":"URI","title":"Directions","uri":"https://www.google.com/maps/search/?api=1&query=<address, URL-encoded>"}` and/or `{"action":"URI","title":"Call","uri":"tel:<digits, leading +>"}` (a phone with fewer than 3 digits gets no Call button). Tapping still opens only the item's link (`url`/`clickAction`), so a place without a link sends `data` with `actions` only. The assignment ping takes the item's `place_id`, adds ` @ <Name>` and the details; an unknown id is ignored. Digest and weekly summary add `   📍 <address> · 📞 <phone>` under each shown line with details (before its 🔗 line). Off: none of this, and the sender is called exactly as before (no `data` without a link).

### 8.2 Housekeeping & drive-time warmer

- **Housekeeping** runs in a single transaction.
  - It deletes tasks where `completed=1` and the **local** date of `completed_at` is more than 60 days ago. A SQL pre-filter narrows the rows, then an exact check runs in Python. Checklist items cascade; open tasks are never touched.
  - It prunes skips and adds dated before today, moves whose `date` and `to_date` are both before today, and `schedule_reminder_log` rows dated before today. It logs INFO if any tasks were removed.
  - Every 60-second tick also runs the app-messages outbox (`app_messages.run_outbox` → `app_bus.run_outbox_once`: re-sends, expiry, pruning, the six-hourly `hello`; §15).
- **Drive-time warmer** picks places that match any of these:
  - `drive_checked_at IS NULL`;
  - failed (`drive_minutes IS NULL`) and last checked **more than 1 h** ago;
  - last checked **more than 30 days** ago;
  - `drive_mode` is NULL or differs from the current setting, so toggling `avoid_tolls` re-queues everything.
  - Changing `home_address` NULLs every place's `drive_minutes`/`drive_checked_at`/`drive_tolls_avoided`; changing a server URL NULLs `drive_checked_at` of failed places.

  Never-checked places go first, then the oldest; `LIMIT 1`.

## 9. Frontend

- **Shell.** `index.html` loads `common/theme-boot.js`, `common/themes.css`, `common/settings.css` and `style.css` in `<head>`, then `common/ui.js`, `common/settings.js`, `common/people.js`, `common/backnav.js`, `common/whoami.js`, `app.js` and `maintenance.js`, each with `?v=<config.yaml version>`. There is no inline script: `common/theme-boot.js` applies the saved `theme` and `sidebarCollapsed` before first paint. URLs are relative only. User text is added only via `h()` / `textContent`, never `innerHTML`. Every storage access is wrapped in try/catch.
- **Shared helpers.** `common/ui.js` (`window.UI`) gives the DOM helper `h()`, `$`, `clear`/`mount`, `lsGet`/`lsSet`, `debounce`, `toast()`, `openModal()`/`confirmDialog()`; the list of open dialogs is `UI.dialogs()` (was the global `modalStack`).
- **API helper.** `api()` (`UI.makeApi` with the `as_user` option) appends `as_user` while acting, unless `asSelf` is set, and throws the server's `detail` message.
- **Chrome.**
  - The sidebar holds the brand, nav, a user chip (`<display> · admin`, opening Settings → "How the app sees you") and a Theme select.
  - Themes are **Midnight** (default, dark, teal accent), **Slate**, **Daylight** and **Auto** (Daylight on a light device, else Midnight), from `common/themes.css`; `style.css` sets the teal accent and the app's own colours per theme. A saved **Ink** becomes Midnight. The select is filled by `HouseholdTheme.bindSelect()`.
  - `‹` / `›` collapse the sidebar to an icon rail.
  - At ≤760 px the sidebar becomes a scrolling bottom icon bar, and Settings gains a mobile-only Appearance card.
- **"No admin yet" banner.** While `whoami.noAdmin` is true, `#noAdminBanner` (under the top bar, on every tab, for everyone; filled by `HouseholdWhoami.fillNoAdminBanner` from `common/whoami.js`) reads "No admin yet — add your Home Assistant user name (**<their user name, or id if no name was sent>**) to `admin_users` on the app's Configuration tab, save, and restart the app." with a **How the app sees you** button that opens Settings and scrolls to that card.
- **Back gesture** (`common/backnav.js`, shared). Back closes the top modal (via its own close, so `onClose` runs); otherwise, away from the Calendar, it returns to the Calendar; only Back on the Calendar with nothing open leaves the app. Tabs and deep links add no history entries.
- **Acting as (admins only).**
  - A top-bar select offers "Myself (name)" plus everyone else; disabled users are marked.
  - While acting, a banner reads "Acting as X — everything you add or change is recorded as them." with a **Switch back to me** button.
  - The choice is stored in `sessionStorage.actAsUserId` and restored on start if that user is still known. A 404 on switching sends you back to yourself with a toast.
- **Tabs.** Calendar (the start tab), Dashboard, Schedule, Lists, Places, Settings, plus **🛡️ Admin** (`hidden` unless `isAdmin`). Hash routes: `#/<tab>`, and `#/admin/settings|users|maintenance|storage` (`#/admin` → settings). The current tab is written with `history.replaceState`; `hashchange` navigates. Short routes `#/users`, `#/storage`, `#/app-settings` (and `showTab("users"|"storage")`) redirect to the matching Admin tab. A non-admin on an admin route sees a card "Only admins can open this page".
- **Links.** `linkChip(url)` renders `<a class="chip btnlike link-chip" target="_blank" rel="noopener noreferrer" title=<url>>🔗 Link</a>` built with `h()`, and only when the value matches `^https?://` (case-insensitive); anything else renders nothing. A rejected link shows the server's 422 message in the form's error line.
- **Task row.**
  - The checkbox completes the task; the title opens the edit modal; notes show under the title.
  - Chips: type (a colour stripe plus emoji); due date (Overdue, Today, or past with a tooltip); priority; **Required**; 📍 place (a Google Maps search link); 🚗 `N min · leave by HH:MM`; 👤 assignee; **🔗 Link** (`linkChip()`, below); the list name (aggregate views only); and `☑ d/t` with a progress bar, which expands the checklist inline.
  - Completed rows show "Completed <date> by <name>".
- **Task form** (quick add's "More options", and the edit modal).
  - Fields: notes; **Link (optional)** (`type=url`, maxlength 2000; blank → null, so an edit can clear it); date; time (disabled with no date); priority; type; place (`placePicker()`, shared with the schedule form), with a **Search places** box above the list (narrows to places whose name or address contains every word typed, shows "N found", picks the first match unless the current choice still matches, says so when nothing matches) and inline **+ New place…** — once created it's selected, so a retry doesn't create it twice — and an "Open in maps · ~N min from home" link; assign-to (only the owner for a personal list, active users for a shared one); in the edit modal a **List** picker (shared lists, then your own) that moves the task, warning first when the assignee will be cleared; Completion (Optional or Required); and a checklist textarea (create only).
  - The edit modal has a live checklist editor that uses the item routes. Any pending add-item text is saved when you press Save.
- **Lists tab.**
  - Lists appear as a grid of cards, shared first, one per row on a phone. Each card shows the name, Shared or Only me, the open count, and a drag grip.
  - "+ New list" opens a dialog for the name and Only me / Shared.
  - The detail view below has **✎ Rename** and **🗑 Delete list** (the confirmation names the open count). Quick add has a **Required** toggle that resets after each add, plus "More options".
  - The toolbar has **Upcoming | Completed**, Sort, and filters for Assignee, Priority, Type, Place and Completion, plus "Clear filters". It is saved per list in `localStorage["taskView:<id>"]`.
  - Dragging works only in Upcoming, with Manual sort and no filters; otherwise a hint explains why. The Completed view notes the 60-day clean-up.
  - Drag and drop uses Pointer Events, with no re-parenting mid-drag and an insertion line. Alt+↑/↓ (and Alt+←/→ in grids) do the same from the keyboard. If a reorder fails, a toast appears and the view reloads.
- **Calendar tab.**
  - Month view is a 42-day grid from the week start. Agenda view shows the next 30 days and is the default at ≤760 px.
  - Toolbar: ‹ ›, Today, and week start (`localStorage.calendarWeekStart`, Sunday by default). Filters: Type, and Assignee (Anyone, Me, Unassigned or a person). Toggle "Show completed", and a **🗓 Schedule items: all / N hidden / hidden ▾** button opening a dialog: a master *Show schedule items* switch (off → `schedule=0`), *Show all* / *Hide all*, and a tick per visible item (`GET /api/schedule`). Hidden items are filtered in the browser (`itemId`) in Month and Agenda, remembered per acting person in `localStorage["calendarSchedule:<user id>"]`; items, reminders and sensors are unaffected. With maintenance on, the dialog can hide maintenance too.
  - Each cell shows up to 3 chips, then "+N more", all-day first, then tasks and schedule entries merged by time. Task chips show the time, type icon and title. Schedule chips show the glyph (all-day only), the assignee's initial and the name, with a timed item's start–end (and 🔒 if private) on a second line. Skipped and moved-away schedule chips are struck through.
  - Day-panel (and agenda) schedule entries add 🕘 time, 👤 person, 🔒 and 🔗 Link chips. Month-cell chips never show the link.
  - The day panel has "Add a task on this date…" (`POST /api/tasks` with `due_date`), then the day's tasks and schedule entries. Entries from today onward offer **Skip**, **Move…** and **Undo**.
  - An "N overdue" banner (from `/api/dashboard`) links to the Dashboard. An undated-tasks footer links to Lists.
- **Dashboard tab.**
  - At the top: quick add to My Tasks, then four tiles — Overdue, Due today, Next 7 days, Open tasks.
  - A "Coming up" strip lists the API's `schedule` entries (with time range and 🔒).
  - Workload cards, plus Unassigned. Admins can click another person's card to act as them.
  - Task groups: Overdue, Due today, and Upcoming (next 7 days).
- **Schedule tab.**
  - A segmented filter **All · Mine · Household** (`assignee` = none / `me` / `unassigned`), kept in `localStorage.scheduleFilter`.
  - Each row shows the glyph, name, rule label, next date (relative), chips (👤 person, 🕘 start–end, 📍 place as a maps link, 🚗 leave-by, 🔒 Private, 🔗 Link), notes, and entity id with a copy button, plus an On now / Off chip, "Next dates ▾", ✎ Edit and 🗑 Delete (the confirmation mentions the entity). An unpublished item shows "Not published" instead of the entity id and On/Off chip.
  - Expanding a row shows the `upcoming` dates with status chips (Skipped, Moved to…, Moved from…, Extra), each with its reason and Skip / Move… / Undo, plus **Add extra date…**.
  - The item form has "Repeats" (Daily, Weekly, Every N weeks, Every N months on a day, Every N years, Nth weekday of the month, Every N days) and weekday toggles.
  - The anchor snaps to the next valid date until you edit it.
  - "Turn the sensor on in advance" runs from Day of only to 7 days before; it is hidden once both Start and End are set, and a hint says the sensor is on only between them.
  - Person fields: **Start**/**End** (time inputs, both or neither), **For** (Household or an active person), **Place** (`placePicker()`), **Only visible to me** (shown only when the item is for the acting user), **Publish to Home Assistant** (defaults on; ticking private unticks it until the user touches it; a warning shows when private and published).
  - The icon is a preset (trash-can-outline, recycle, leaf, calendar-clock, bell) or a custom `mdi:` name. There are also **Link (optional)** (as in the task form) and notes fields.
  - An "always on" warning appears when the lead is at least the minimum gap. Changing the rule or anchor asks for confirmation, because it clears skips and moves. `buildRule()` builds the rule string; the server validates it.
- **Places tab.**
  - A search box (200 ms debounce) and "+ New place" (name, address, phone).
  - Each row shows the name with "used by N tasks", the address, and 📞 as a `tel:` link.
  - The drive line reads "🚗 Drive time not calculated yet", or "🚗 ~N min from home" plus " · no toll roads" or " · uses toll roads (no toll-free route found)".
  - Row buttons: Open in maps, Copy address, **🔄 Recalculate drive time** (shows "Calculating…" while running), edit, and delete (the confirmation gives the task count).
- **Settings tab.**
  - **Reminders** (replaced by a note while acting) shows a warning box if `canNotify` is false, then:
    - the Daily digest toggle (only the digest), "Send at", and "Include tasks due" (On the day to 7 days ahead);
    - Notify on assignment;
    - Per-task reminders: chips plus an amount and unit (hours, minutes or days), up to 5;
    - the Weekly summary toggle, with day and time;
    - Send test notification.
    - A hint at the top says the digest, weekly summary and per-task reminders also cover schedule items that are for you, private ones included.
  - **How the app sees you** (`WHOAMI_PAGE_SPEC.md`, drawn by `HouseholdWhoami.panel()` from `common/whoami.js`) shows the user name (or "not sent") and user id, each copyable; the display name, marked "not used for matching"; Administrator yes/no; the `admin_users` count; and Reminder service linked yes/no. It gives the shared advice for `admin_users`; when no service is linked it says to ask an admin (Admin → Users), or for an admin links there.
  - **Task types** can be edited and deleted, each with an emoji (up to 8 chars) and an optional colour.
- **Admin.** Page head "Admin", then a tab row **App settings | Users | Maintenance | Storage** (`.admin-tabs`, scrolls horizontally on narrow screens). All calls use `asSelf`.
  - **App settings.** Drawn by `common/settings.js` (`SettingsPage.render`) from the payload's `meta` and `groups`: one card per group, a help line with the range and default under each setting, wrong numbers flagged at the field before saving. A hint that each person sets their own daily reminder time (Settings → Reminders → Send at, 08:00 by default), then the groups Home Assistant sensors (switch, number 1–1440), Drive time (the **Drive times (uses OpenStreetMap services)** switch with a privacy note — what is sent, to which hosts, the configured ones, OpenStreetMap's public services by default, and that self-hosted servers can be entered — then, only while the switch is on (`showIf`, live), the home address, two URL inputs with the defaults as placeholders and the avoid-tolls switch), Reminders (place details) and Maintenance (the files folder with its status, **Check** / **Use this folder** and the confirm before switching). A note that `admin_users` stays in the Configuration tab. **Save** (PUT of changed keys only; disabled until something changed, "N unsaved changes") and **Discard changes**; a 422 shows its message. A "restart needed" chip would show for any `restartRequired` key (none today).
  - **Users.** The shared people list (`common/people.js`, `PeoplePage.render`): an intro, *Check Home Assistant again*, then one card per person: name, "you", login name and first seen; the Active/Disabled switch (disabled on your own row); the notify editor — *Phones* (from Home Assistant, read-only), *Also* (the extra notify services, ✕ removes), *Add* (a select of HA's services minus ones they have, plus a typed name; or only the text box when `available` is false, with a warning showing the error and "Try again" (`refresh=1`); client check `^notify\.[a-z0-9_]+$`, bare names get `notify.`) and **Send a test**.
  - **Storage.** A "Download backup (.db)" button. Restore has a file input, a red warning and a `confirm()`; afterwards the page reloads its lookups.

## 10. Invariants & pitfalls

- **Access.** Never add `ports:`, and keep the ingress IP allowlist: the headers are trustworthy only because of it. Admin authority never passes to the acting user, and prefs never follow `as_user`. The server enforces all of this; the UI only hides things.
- **Time.** Use `config.today()` / `now()` everywhere, never `date.today()`. Alpine needs `tzdata`, or the zone silently stays on the fallback.
- **SQLite.** Every connection needs `foreign_keys=ON`, or cascades and SET NULL silently don't run. It also needs `normalize_addr` registered — including in `validate_backup_file` before `integrity_check` — or the expression index makes every backup look "not a valid SQLite database".
- **Migrations.** Schema changes are additive: add each column to `SCHEMA` **and** `db.MIGRATIONS` (applied first in `_migrate()` by `db_core.add_missing_columns`). `digest_time` is filled by `_migrate()` (old household `reminder_time` if one was stored, else 08:00), so nobody's time jumps.
- **Restore**, in order:
  1. Write the upload to a scratch file **in `DATA_DIR`** (`backup_core.receive`; `os.replace` fails with EXDEV across filesystems).
  2. Validate with `integrity_check`, and require all 14 tables (`db_core.validate_file`); otherwise return 400.
  3. Take `_import_lock` and checkpoint the WAL with `wal_checkpoint(TRUNCATE)`.
  4. `os.replace` the scratch file over the database.
  5. Delete the `-wal` and `-shm` files (3–5: `db_core.swap_in`, run by `backup_core.restore_file`).
  6. Run **`init_db()`** again, so an older backup is migrated immediately.
  7. `init_db()` bumps `db.generation()`, so App settings reload from the restored file; `geocode.reset_home()`; then as request **BackgroundTasks** (tied to the request, never untracked executor jobs) `apply_exposure_change_blocking()` (full sync, or remove all if the backup had exposure off) and a home re-geocode.
- **Backup** always uses `Connection.backup()` (`db_core.snapshot_to_tempfile`, sent by `backup_core.send_file`), never the raw file (the database is in WAL mode).
- **Network I/O.** No request path or background lookup does network I/O while holding a DB connection: Recalculate, the drive-time warmer, the notify-services list, the admin test send and the startup notify check all close theirs first. Requests only ever read the cached `drive_minutes`. (The reminder passes still send while their read connection is open — unchanged, harmless under WAL.)
- **Settings are read live.** Never copy an App setting into a module constant; call `settings.get()` (cached) where it's used.
- **Routes and caching.** `/lists/reorder` must be declared before `/lists/{id}`, and the static mount goes **last**. `/` and `*.html` are served with `no-cache, no-store, must-revalidate` plus `Pragma: no-cache` (`web_security`); assets are versioned with `?v=`.
- **Client limits mirror server limits** (e.g. the exception reason input is `maxlength=60`, matching the server's 60-char check; link inputs are `maxlength=2000`); keep them in step.
- **Links.** The server's link rule is the enforcement; the UI still renders an `<a>` only for `http(s)://` values, always with `target="_blank" rel="noopener noreferrer"`, and never via `innerHTML`. Links are not sensor attributes.
- **Private schedule items.** Every query of `schedule_items` on a request path must filter with `VISIBLE_SQL` (or `_load_item`); reminders select by `assigned_to` only. Anything new that reads items must do the same.
- **Sidebar arrow.** JS swaps `‹`/`›`; don't also rotate it in CSS.

## 11. Build, test & release

- **Dependencies (pinned).**
  - Runtime: `fastapi==0.141.1`, `uvicorn==0.53.0`, `python-multipart==0.0.32`.
  - Dev: adds `httpx2==2.13.1`.
  - Bump them together with the sibling apps.
- **Running tests.** `pip install -r requirements-dev.txt`, then `python3 -m unittest discover -s tests`. Every module runs with the pinned dependencies; `tests/test_packaging.py` also checks `config.yaml`, the `?v=` strings, the docs, the icons and that no personal details are in any text file (the shared checks come from `common_tests/packaging_core.py`, run with the app's own values). `tests/test_security_headers.py` checks the CSP and `nosniff` and that the page has no inline scripts, handlers or style attributes.
- **Test harness.**
  - `_env.py` must be imported first (built on `common_tests/env.py`). It sets a temp `DATA_DIR`, `OPTIONS_PATH`, `SUPERVISOR_TOKEN`, `DEV_ADMINS=adminy` and `BACKGROUND_LOOPS=0`. Every test deletes and recreates the database, so no loop may run behind the tests' backs (a loop's first tick in a worker thread would race the reset: random `disk I/O error` / `no such table`); tests call the passes and `ha_sensors.loop()` directly.
  - `common_tests/fake_ha.py` (shared) is an in-process server for `/config`, `/states`, `/services` and notify. It records requests, and its `notify_services` / `notify_entities` settings control which targets exist.
  - Tests patch `config.utcnow` and `SUPERVISOR_CORE_API`. HTTP tests use `TestClient(app, client=("127.0.0.1", 12345))` (`common_tests/ingress.py` has the same as `ingress_client` and the header helpers). `tests/common_tests/` also runs the shared modules' own tests (whoami, auth_core, db_core, settings_core, people_admin, web_security, backup_core, sensor_publisher, geo, csv_export) and `test_shared_copies.py`. Settings are changed with `settings.update(...)` (each test starts from a fresh DB, so defaults — Drive times off; tests that use drive time turn it on); notify links with a `link()` helper that inserts into `user_notify` (in `test_core` it waits for `add_user` if the person doesn't exist yet).
- **Coverage:** every route and access rule (including private schedule items); the link rule, links in every notification kind and the action-vs-entity `data` split; recurrence and exceptions; timed sensor windows and the per-item switch; all reminder kinds, for tasks and schedule items; housekeeping in non-UTC zones; drive time (unit stripping, tolls, Recalculate, the Drive times switch: defaults, the existing-data rule, nothing sent while off); the "No admin yet" flag and banner; packaging and the personal-details scan; notify fallback and hints; backup/restore including migration of an older backup; App settings (admin-only, 422 cases, partial updates, each setting taking effect live incl. the sensor loop), `config.yaml`/translations holding only `admin_users` and old options.json keys being ignored, the `digest_time` migration, admin-only guards, the notify-services list (cache, errors), assignment CRUD, reminders to every service, and the admin test send.
- **Release.**
  1. Bump `version` in `config.yaml` **and** every `?v=` string in `index.html` (`test_packaging` checks they match).
  2. Rebuild.
  3. Restart after changing `admin_users` (App settings need no restart).

## 12. Possible future work

Ideas that are deliberately not built:

- `todo.*` and `calendar.*` entities in Home Assistant (today only schedule and maintenance `binary_sensor`/`sensor` entities are published).
- Recurring tasks — recurring *chores to tick off*, which would need generation, completion and overdue rules (see §13 for why schedule items aren't that).
- Changing a list's kind (shared ↔ personal), per-list permissions, tags.
- Notification snooze or actionable notifications.

## 13. Personal schedule items

Recurring appointments for one person — "Yoga, every Tue, Thu & Fri 18:00–19:00 @ Studio" is `weeks:1:2,4,5` plus times, a place and an assignee.

**Decision: extend schedule items; don't add recurring tasks.** The rule engine, exceptions (skip a cancelled class, move one, add an extra) and calendar are reused unchanged, and an appointment has nothing to tick off and never goes overdue. Recurring tasks would need generation, completion and overdue rules, and missed sessions would pile up as overdue. Recurring *chores that need ticking off* stay out of scope.

Where the pieces live: columns and log table §5, field rules and timed state §5.4, privacy §4, routes §6, sensors §7.2, reminders §8.1, UI §9. Defaults: new private items aren't published; household items (assigned or not) stay editable by anyone; items from older databases migrate as household, all-day and published.


## 14. Maintenance

House upkeep. Off until an admin turns it on (`maintenance_enabled`); while off every non-admin maintenance route answers **409**, the tab is hidden (`whoami.maintenance.enabled`) and nothing is sent.

**Decision: a table of its own, not schedule items.** Upkeep is marked done, can be overdue and usually repeats *after it was done*, none of which schedule items do. It still shows in the Schedule tab (its own collapsed group) and on the Calendar, and calendar-mode items reuse the rule grammar (§5.3). One-off jobs are ordinary tasks in the shared list with `lists.role='maintenance'` (created on first enable, undeletable while on — 409).

### 14.1 Items (`maint_items`)
- `mode='interval'`: `every_n` + `every_unit` (day 1–365, week 1–52, month 1–36, year 1–10); due = `last_done` + interval (months clamp to month end), or `start_date` when never done (create without `last_done` → today).
- `mode='calendar'`: `rule` + `anchor_date`; due = first occurrence after `cleared_through` (from the anchor). Mark done sets `cleared_through` = max(current due, last occurrence ≤ max(date, today)); a rule/anchor change clears it.
- Snooze: `snooze_due` (the due it applies to) → `snooze_to`; `{days}` from max(today, due), `{until}` > today and > due, ≤ 366 days; `{until:null}` cancels; Mark done clears it. Paused → no due date.
- Status: overdue (< today), due (≤ `lead_days` 0–60 ahead), upcoming, paused.
- Fields: name 1–60, icon (an emoji), category (`maint_catalog.CATEGORIES`), notes ≤1000, howto ≤2000, url (link rule), place, assignee, `recipients_mode` default|custom (+ `maint_recipients`), `overdue_every` 0/3/7/14, `expose_sensor`, `suggestion_key` (`b:<key>`/`c:<id>`). Max 200 items.
- **Mark done** (`maint_done`): date ≤ today within 10 years, note ≤500, cost 0–10,000,000 → `cost_cents`, `due_was`, `prev_state` (JSON of last_done/start_date/cleared_through/snooze). Interval: `last_done` = max(existing, date). **Undo** (latest record by `created_at`) restores `prev_state`, deletes the record and moves its files to `_deleted` (503 while the folder is offline and it has files).

### 14.2 Suggestions
`maint_catalog.BUILTIN` (~45) + `maint_suggestions` (admin CRUD). Shown if `needs` is in `maintenance_profile` (or has none), not hidden (`maint_hidden`, household-wide), and no item has its key or its name (case-insensitive). Seasonal ones (`seasons`) default to calendar mode due on the first day of each season: one season → `years:1`, two six months apart → `months:6:1`, else `months:3:1`, anchored at the next start. Seasons are meteorological (N: Mar/Jun/Sep/Dec; S: +6 months, from HA's `latitude` < 0). In-season (now or starting within 31 days) first.

### 14.3 Notifications (`maint_notify.py`, the reminders loop)
Recipients per item = custom list or `maintenance_recipients` (+ assignee), filtered to enabled users with `maintenance_notify` ≠ 0 — independent of the digest switch. In the 60 minutes after each person's `digest_time` (08:00 default) one grouped message: overdue (first time, then every `overdue_every` days; 0 = never), due today, due soon (≤ lead_days) — each once per due date; unassigned jobs in the list due today (household recipients); the season's fitting suggestions once per season in its first 14 days. Logged per line in `maint_notify_log` only when a send succeeded. Notify `data` url/clickAction = the app's ingress panel when `HOSTNAME` gives it. The weekly summary appends a "Maintenance:" section (overdue or due within 7 days). The daily digest doesn't repeat them.

### 14.4 Calendar
`GET /api/calendar` returns `maintenance` (param `maintenance=0` omits it): per item the real due date (overdue shows on today, status overdue/due/upcoming) and projected later ones (`projected: true`) in range, filtered by `assignee` like schedule items.

### 14.5 Files (`maint_files.py`)
`maintenance_files_path` ("" = off) must be inside `SHARE_ROOT` (/share), no `.`/`..`. `.household_todo_store` marker = `maint_files_store_id` (app_settings, not an App setting). Saving a new path runs `inspect` (refused: other install / missing parent / read-only → 409; files already attached or a non-empty folder → 409 unless `confirm`) then `check(allow_setup=True)`. `check()` at start-up and every 5 minutes; requests re-check an offline folder at most every 30 s; 503 with the reason while offline. Owners: item, done record or job (task in the list). Names cleaned for Samba, duplicates get " (2)"; ≤25 MB (413), empty → 422; JPEG/PNG/WebP/GIF get a 320 px `_thumbs` preview. Downloads: images/PDF/text inline, others attachment, `CSP: sandbox`, `nosniff`. Deletes move to `_deleted/<date>/…` (housekeeping empties dated folders older than 30 days); purged jobs' file rows are dropped, their files stay. Renames reconcile item folders when online. A DB restore keeps the current path and store id.

### 14.6 API
`GET /api/maintenance` (items, jobs, files status, overdueCount, currency); `POST/PATCH/DELETE /api/maintenance/items[/{id}]`; `POST …/{id}/done|undo|snooze`; `GET /api/maintenance/history[.csv]?year&category&item` (the CSV, written by `app/common/csv_export.py` with a UTF-8 BOM: Date, Item, Category, Done by, Note, Cost, Was due, Files; **formula guard**: the Item, Category, Done by, Note and Files cells starting with `=` `+` `-` `@` (or tab/CR) get a leading `'` — new; Date, Cost and Was due unchanged); `GET /api/maintenance/suggestions`; `PUT/DELETE /api/maintenance/suggestions/{key}/hidden`; `POST /api/maintenance/files?item_id|done_id|task_id`, `GET /api/maintenance/files?task_id`, `GET|DELETE /api/maintenance/files/{id}[?thumb=1]`. Admin: `GET/PUT /api/admin/maintenance` ({enabled, recipients, profile, sensor}), `POST/PATCH/DELETE /api/admin/maintenance/suggestions[/{id}]`, `POST /api/admin/settings/check-maintenance-folder`, `POST /api/admin/maintenance/files/check` ({useThisFolder, confirm}). `/api/prefs` gains `maintenanceNotify` and `maintenanceRecipient`.

### 14.7 Sensors
`push_maintenance_blocking` (after changes, and every `sensor_refresh_minutes`): the overdue count sensor while `maintenance_sensor` (deleted once when off), and per item with `expose_sensor` a binary_sensor on while due/overdue — only while `expose_schedule_sensors` is on too.

## 15. Checklists from Household Docs (app messages)

Household Docs' *Make a Todo list* / *Send to Todo* (Docs spec §17.16) reaches Todo as app messages over Home
Assistant's event bus (`APP_MESSAGES_SPEC.md`; the kinds and their checks are §6.4 there). `app_messages.py`:

- **Connection.** The lifespan starts the bus (`app_bus.start`, its own WebSocket — Todo has no other —
  `outbox_thread=False`) together with the background loops (so not under `BACKGROUND_LOOPS=0`, the tests) and
  stops it on shutdown; without a Supervisor token it stays off. The outbox runs in the housekeeping loop.
  `hello` says `can: ["todo.items.add", "todo.lists.list"]`, version `config.APP_VERSION` (= config.yaml's).
- **`requested_by` is the actor.** A known Todo user (opened the app at least once) who isn't disabled in Admin →
  Users, else `nack not_allowed no_access`. "Acting as" never applies. Lists as in `GET /api/lists`: every
  shared list (the Maintenance list flagged) and their own personal lists; anyone else's private list is
  `nack not_found list`, like a missing one.
- **`todo.lists.list`** → `{lists: [{id, name, kind, open, maintenance?}], more}` (trimmed to fit 8 KB).
- **`todo.items.add`** — `list_id` or `new: {name 1–60, shared?}` (a new personal list of `requested_by`, or a
  shared one, placed last). Items 1–200, text 1–200 after trimming (the task-title and checklist-item limits),
  `done` bool, `level` 0/1: level 0 → a task at the end of the list (`created_by` = requested_by, `source` =
  the message's `source` or the sender's name without "Household "), `done` → completed now by requested_by;
  level 1 → a checklist item of the task before it (≤ 100 per task, else `nack invalid too_many_subitems`); a
  level 1 item with no task before it is a task. All in the transaction that records the answer: any refusal
  writes nothing (a list made by `new` included). Answer `{list_id, list_name, created, tasks, subitems, ids?}`
  (`ids` dropped if the answer wouldn't fit). No notifications (nothing is assigned; the only "new task" push is
  the assignment ping). During a restore (`db._import_lock` held) every message is `nack busy`.
- **Frontend.** A task with `source` shows a "from <source>" chip. Admin → App settings ends with the read-only
  **Connected apps** card (`common/connected-apps.js`).
- **Privacy.** Item texts the person chose to send travel in the event, so DOCS.md shows the recorder exclusion
  (APP_MESSAGES_SPEC §8).
- **Tests.** `tests/test_app_messages.py`: every check of both handlers (allowed and refused, nothing written on
  a nack, answer size, the restore lock), the resulting tasks in the API, no notifications, Connected apps, and
  end to end on the fake event bus (`common_tests/fake_ha_bus.py`) with a fake Household Docs: lists, a new list
  and a second batch, a duplicate acted on once, a refusal, the lifespan starting and stopping the bus; plus
  `common_tests/test_app_bus.py` on the app's own copies.

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
