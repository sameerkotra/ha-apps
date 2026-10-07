# Calorie Tracker — rebuild spec

The code is the source of truth. Schema: `calorie_tracker_schema.sql` (next to this file). User guide: `../DOCS.md`. Shared conventions (repository root, where present): `HA_ADDON_PATTERNS.md` §4, `WHOAMI_PAGE_SPEC.md`, `SHARING_AN_ADDON.md` §2–§3.

## 1. Purpose & scope
- Home Assistant app (ingress-only) for tracking food, macros, weight and goals for each household member, with optional AI macro estimation and chat through a provider the admin chooses: Ollama, an OpenAI-compatible service or Anthropic Claude ("bring your own AI"). Without AI set up, everything else works.
- Your HA login is your account. There is no login screen and there are no local accounts. A user row is created the first time someone opens the panel.
- Admins listed in `admin_users` can act as another user and open the **🛡️ Admin** page: App settings (AI provider/address/model/key, sensor switch), Users (hide users from the switcher) and Storage (back up or restore the database). While `admin_users` is empty, every page shows a "No admin yet" banner; nobody is promoted automatically.
- The only data sent to HA is each user's calorie total for today, as a sensor. Macros, weight and food names never leave the app. The AI provider receives only the typed food description (estimate) or chat message.

## 2. Stack & file layout
- Python 3.12 on `python:3.12-alpine` + `tzdata`. FastAPI + uvicorn in one process on port 8099, serving `/api/*` and the static frontend.
- Stdlib `sqlite3` (no ORM) and stdlib `urllib` for HA and AI calls (no httpx). Blocking calls run through `run_in_threadpool`.
- Frontend: plain HTML/CSS/JS with no framework, no build step, and no external fonts or CDNs. Icons are emoji.
```
calorie_tracker/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore
├── README.md (store page)  DOCS.md (user guide)  CHANGELOG.md  translations/en.yaml  icon.png  logo.png
├── spec/                  SPEC.md, calorie_tracker_schema.sql
├── tests/                 _env.py, test_app.py, test_ai_providers.py, test_security_headers.py, test_packaging.py
│   └── common_tests/      shared test helpers and shared-module tests (copies, see below)
└── app/
    ├── main.py            lifespan (jobs), ingress gate, security headers (CSP), router registration, static mount
    ├── config.py          /data/options.json → admin_users; HA time zone (config.ZONE: now/today)
    ├── settings.py        App settings declared on common settings_core: SETTINGS, GROUPS, secret key, "outside the home network"
    ├── db.py              SCHEMA, init_db/MIGRATIONS, get_conn, backup/validate/import (on common db_core)
    ├── auth.py            get_current_user / get_acting_user / require_admin (on common auth_core)
    ├── ha_sync.py         daily-calories sensor push/remove (live App setting, common sensor_publisher); HA time zone fetch
    ├── ai_client.py       the app's AI layer on common ai_client: Config, current(), generate, warm-up, estimate, chat, status, privacy, WORDING
    ├── schemas.py         Pydantic request bodies
    ├── routers/           me, users, goals, weight, saved_foods, logs, ai, admin
    ├── common/            shared Python (copies): whoami, ha_client, ha_time, housekeeping, auth_core, db_core,
    │                      settings_core, web_security, backup_core, sensor_publisher, ai_client
    └── static/            index.html, app.js, style.css
        └── common/        shared browser files (copies): theme-boot.js, themes.css, ui.js, settings.js, settings.css,
                           people.js, backnav.js, whoami.js
```
- `app/common/`, `app/static/common/` and `tests/common_tests/` are copies of the repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see `common/README.md`). Never edit a copy: edit `common/` and re-sync; `tests/common_tests/test_shared_copies.py` fails if a copy was changed.

## 3. Manifest & options
`config.yaml` sets: `name: "Calorie Tracker"`, `version: "2.2.1"`, `slug: calorie_tracker`, a one-to-two-sentence `description`, `url: https://github.com/sameerkotra/ha-apps`, `arch: [amd64, aarch64]`, `startup: application`, `boot: auto`, `init: true`, `ingress: true`, `ingress_port: 8099`, `panel_icon: mdi:food-apple`, `panel_title: Calorie Tracker`, and `panel_admin: false`, so every HA user sees the panel. The only API permission is `homeassistant_api: true`. `hassio_api`, `auth_api`, `docker_api` and `full_access` are all false, and `apparmor: true`. There is no `ports:` key and no host networking.

| Option | Schema | Default | Effect |
|---|---|---|---|
| `admin_users` | `[str]` | `[]` | HA user ids or login names. Matching is case-insensitive and ignores surrounding whitespace. An empty list means nobody is an admin and every page shows "No admin yet" |

- `options:` and `schema:` contain only `admin_users`, and `translations/en.yaml` `configuration:` has only `admin_users` (name "Admins"; the description says display names are not accepted, that the list is who can open the Admin page, that an empty list shows "No admin yet", and points to "How the app sees you").
- `config.py` reads `$DATA_DIR/options.json` (= `/data/options.json` in production) once at import time, without bashio or an entrypoint script, and only for `admin_users`; it ignores every other key. A `null` or non-list value falls back to the default, and an unreadable file means every option uses its default. **Changing options requires an app restart** (Information tab → **Restart**).
- Environment variables: `DATA_DIR` (default `/data`; the DB is `$DATA_DIR/calorie.db`, the options file `$DATA_DIR/options.json`), `SUPERVISOR_TOKEN` (injected by the Supervisor), and `DEV_ADMIN_USERS` (a comma list merged into the admin set, used by the tests and a local dev server).

### 3a. App settings (Admin → App settings, `app/settings.py`)
| Key | Default | Validation | Used by |
|---|---|---|---|
| `ai_provider` | `""` (not set up) | strict string: `""`, `ollama`, `openai`, `anthropic` | every AI call, `/api/ai/status` |
| `ai_url` | `""` | strict string ≤500, stripped; blank, or `http`/`https` with a host; no spaces, user/password, query or fragment; valid port; trailing `/` stripped. Required when the provider is `ollama` | blank = the provider's standard address: `https://api.openai.com/v1` (openai), `https://api.anthropic.com` (anthropic); Ollama has none |
| `ai_model` | `""` | strict string ≤200, stripped, no whitespace | every AI call |
| `ai_api_key` | `""` | **secret**; strict string ≤500, printable ASCII without spaces (header-safe); the error never echoes it | openai (`Authorization: Bearer`, only if set), anthropic (`x-api-key`, required) |
| `ai_max_tokens` | `4096` | strict int 256–200000 | Anthropic `max_tokens` (others ignore it) |
| `expose_daily_calories_sensor` | `true` | strict bool (`"yes"`, `1`, `null` → 422) | every sensor push (`ha_sync.exposed()`); a change triggers publish-all / remove-all (§7) |

- `settings.py` declares the list once — `SETTINGS` (`Setting(key, default, label, help=, group=, min=, max=, choices=, secret=, …)`) and `GROUPS` ("🤖 AI", "🏠 Home Assistant") — and builds `REGISTRY = settings_core.Registry(...)` (`app/common/settings_core.py`) with its own hooks: `prepare` (the secret rules below), `check` (Ollama needs an address), the readable error format. The registry provides validation, storage, the cache and the GET/PUT payload; labels and help text come from the server.
- Stored in the `app_settings` table (JSON `value`, `updated_at`, `updated_by` = admin's HA user id). A key without a row (or with a value of the wrong JSON type — the `isinstance` check of stored rows) uses `DEFAULTS`. Pydantic model `AppSettings` (`REGISTRY.model`: `extra="forbid"`, `str_strip_whitespace`, strict types) plus one cross-field rule (Ollama needs an address).
- `settings.get(key)` / `all()` read through an in-memory cache, dropped on every write and whenever `db.init_db()` bumps `db.generation` (startup, restore) — so a restore's settings apply immediately. Every key is `restartRequired: false`.
- `settings.update(partial, user)`: unknown keys → error; validates `{current values + partial}`; writes only the keys whose value changed; logs the changed key **names** only. **Secret rules:** a blank (or whitespace/`null`) `ai_api_key` keeps the saved key; `clear_ai_api_key: true` removes it. The key is never returned by the API (`payload()` has `secrets.ai_api_key = {saved, hint}`, hint = `…` + last 4 characters when the key is ≥12 long, else `saved`), never logged, and blanked in database downloads (§10). When any `ai_*` key changes, `ai_client.forget_state()` clears the warm timestamp, last error and last request.
- `ai_configured()`: provider set, resolved address non-empty, model non-empty, and (anthropic only) a key. `ai_is_external()`: the resolved address's host is outside the home network — an IP that isn't private/loopback/link-local, or a name that isn't `localhost`, single-label, or ending in `.local .lan .home .internal .localdomain .home.arpa .localhost .test .invalid`.
- `Dockerfile`: `ARG BUILD_ARCH`/`BUILD_VERSION` → `FROM python:3.12-alpine` → `apk add --no-cache tzdata` → `pip install -r requirements.txt` → `COPY app` → `io.hass.version/type="app"/arch` labels → `EXPOSE 8099` → `CMD python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099`.

## 4. Security, identity & admin
- **Ingress-origin gate** (`require_ha_ingress_auth` middleware, `auth_core.refuse_outsiders`): if `request.client.host` is not in `auth_core.INGRESS_HOSTS` (`{172.30.32.2, 127.0.0.1, ::1}`), the request gets 403 `{"detail":"Forbidden — access only via Home Assistant"}`. The gate covers every path, including static files and `/api/health`. It checks the network origin only, and each route's dependency still returns its own 401. Why: another app on the internal Docker network could otherwise forge the headers.
- **Security headers** (`web_security.SecurityHeaders(CSP, pragma=True).install(app)`, policy `CSP` in `main.py`): every response gets `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'` and `X-Content-Type-Options: nosniff`. No inline scripts or `on…=` handlers in the page.
- **Headers** (trusted only because of the gate above):

| Header | Use |
|---|---|
| `X-Remote-User-Id` | Primary key and admin match. If missing: 401 "No Home Assistant user identified. Open Calorie Tracker from its panel in the Home Assistant sidebar." |
| `X-Remote-User-Name` | Login name. Used for the admin match and shown on the whoami page. May be absent |
| `X-Remote-User-Display-Name` | Shown in the UI and used for the sensor slug. Falls back to the login name, then to `"Home Assistant User"` |

- **`get_current_user`** upserts `users(id, name)` (the name is refreshed and `enabled` is left alone), runs `INSERT OR IGNORE` on `goals(user_id)`, and returns `{id, name, slug, is_admin, username}`. `slug` = lowercase, each run of non-`[a-z0-9]` characters replaced by `_`, trimmed; `"user"` if nothing is left. There is no dev or test-user fallback.
- **Admin** = `{user_id, username}` intersected with `admin_users` (both sides lowercased). **Display names are never matched.** If only the display name is listed, the log gets a WARNING naming the id and login to use instead, at most once per user id per process, and `/api/whoami` returns `displayNameOnly: true`. The list is static because real HA admin status would require `hassio_role: admin`, which is too much access for a UI gate.
- **`get_acting_user`** reads the `?as_user=<id>` query parameter:
  - No parameter, or the caller's own id: acts as the caller.
  - Caller is not an admin: acts as the caller silently, with no error.
  - Caller is an admin but the id is unknown: acts as the caller.
  - Otherwise: acts as that user, `{id, name, slug, is_admin: <caller's>}`. The `enabled` flag is not checked.
- **`require_admin`**: 403 "Only admins can do this. See admin_users on the app's Configuration tab."
- **No admin yet**: `auth.no_admins()` is true while `admin_users` is empty. `/api/me` returns `noAdmin` and `username` (login name, else the user id); the frontend then shows the banner (§9) to everyone. Nobody is ever promoted automatically.
- Enforcement is server-side. The UI hiding admin controls is only a convenience. The AI routes and `/api/me`/`/api/whoami` always use the real caller.

## 5. Data model
Six tables (full DDL with comments in the `.sql` file): `users` (HA id, display name, `enabled`), `goals` (1:1; defaults 2000 kcal / 150 P / 200 C / 65 F; optional starting and target kg), `weight_logs`, `saved_foods` (per-user templates plus `notes`), `food_logs` (optional `saved_food_id`, `ON DELETE SET NULL`), and `app_settings` (key → JSON value, §3a). Indexes are on `(user_id, date)` for both log tables.
- `init_db()` runs `executescript(SCHEMA)` followed by `_migrate()`, then increments `db.generation`. It runs at startup and again after every restore (so an older backup gains `app_settings` via `CREATE TABLE IF NOT EXISTS`). `validate_backup_file` still requires only the original five tables. `_migrate()` adds the columns in `db.MIGRATIONS` (`users.enabled`, `saved_foods.notes`) through `db_core.add_missing_columns`, and turns the older `ollama_url` / `ollama_model` rows into `ai_provider = "ollama"`, `ai_url`, `ai_model` (`INSERT OR IGNORE`, keeping their `updated_at`/`updated_by`, so an existing `ai_*` value wins), then deletes the old rows — for an install's own database and for a restored older backup alike. An older database that never stored those rows starts with AI not set up. **To add a new column, update both the CREATE statement and `db.MIGRATIONS`.**
- `get_conn()` opens a short-lived connection for each use (`db_core.connect` + `db_core.transaction`, `app/common/db_core.py`): `timeout=10`, `sqlite3.Row` rows, `PRAGMA foreign_keys = ON` on every connection, commit on success, rollback on exception, always closed. WAL journal mode.
- Dates are ISO `YYYY-MM-DD` strings in HA's time zone, compared as strings.
- A `food_logs` row is a standalone copy of the numbers. `calories`/macros are the entry's totals, and `servings` is display-only (nothing multiplies by it).
- Every query is scoped to the acting user's id. Update and delete use `WHERE id=? AND user_id=?` and return 404 when no row matches, so ids from other users are rejected.
- Users are never deleted, and admin status is never stored.

## 6. API
Auth column: **none**, **cur** = `get_current_user`, **act** = `get_acting_user` (accepts `as_user`), **adm** = `require_admin`. Bad input from Pydantic or a query-parameter pattern gets 422.

| Method & path | Auth | Behavior |
|---|---|---|
| `GET /api/health` | none | `{"status":"ok"}` |
| `GET /api/today` | none | `{today: "YYYY-MM-DD", timezone: "<IANA>"}` from `config.today()` |
| `GET /api/me` | cur | `{id, name, is_admin, username, noAdmin}` |
| `GET /api/whoami` | cur | The shared contract (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`): `{haUserId, haUsername, haDisplayName, nameSent, isAdmin, displayNameOnly, adminEntries (count only), noAdmin, viaIngress (X-Ingress-Path present), extras: []}`. Never returns the list itself or raw headers |
| `GET /api/users` | adm | `[{id, name, enabled, is_you}]`, sorted by name case-insensitively |
| `PUT /api/users/{id}` | adm | Body `{enabled: bool}`. Returns the updated row; 404 if the id is unknown. Only affects visibility in the switcher |
| `GET /api/goals` | act | The goals row |
| `PUT /api/goals` | act | Partial update of the fields sent (`exclude_unset`). Returns the row. Sending `null` for a macro violates NOT NULL |
| `GET /api/weight` | act | All entries, oldest date first |
| `GET /api/weight/history?granularity=daily\|weekly\|monthly` | act | `{granularity, target, points:[{label, date, weight}]}`. Daily: every entry from the last 30 days, labelled `M/D`. Weekly: mean per 7-day bucket starting at today−83 days (12 buckets). Monthly: mean per calendar month over the last 12 months. Empty buckets are skipped, not zero-filled. Values rounded to 1 decimal |
| `POST /api/weight` | act | Body `{date, weight_kg, note?}`. Returns the new row |
| `DELETE /api/weight/{id}` | act | `{ok:true}`; 404 if not the acting user's entry |
| `GET /api/saved-foods` | act | Sorted by name |
| `POST /api/saved-foods` | act | Body `{name, serving_size=1, serving_unit="serving", calories, protein=0, carbs=0, fat=0, notes?}`. An empty `notes` is stored as NULL |
| `PUT /api/saved-foods/{id}` | act | Full replace with the same body. 404 if not the acting user's food |
| `DELETE /api/saved-foods/{id}` | act | `{ok:true}`; 404 if not the acting user's food |
| `GET /api/logs?date=` | act | That day's entries, ordered by `created_at` |
| `POST /api/logs` | act | Body `{date, meal_type="snack", food_name, servings=1, calories, protein=0, carbs=0, fat=0, saved_food_id?}`. Pushes the sensor if `date` is today |
| `DELETE /api/logs/{id}` | act | `{ok:true}`; 404 if not the acting user's entry. Pushes the sensor if the entry was dated today |
| `GET /api/summary?date=` | act | `{date, totals{calories,protein,carbs,fat}, goal{calorie_goal,protein_goal,carb_goal,fat_goal}, remaining{…}, latest_weight_kg}`. The latest weight is the most recent overall, not limited to on or before `date` |
| `GET /api/summary/month?month=YYYY-MM` | act | `{month, label "September 2026", is_current, is_future, days_in_month, days_elapsed, totals, avg_per_day, goal, weight{avg_kg,start_kg,end_kg,change_kg,entries}}`. `days_elapsed` counts up to today and is 0 for a future month. `avg_per_day` = total ÷ `days_elapsed` |
| `GET /api/history?granularity=` | act | `{granularity, goal, points:[{label, date, calories, goal}]}`. Daily: last 14 days, labelled `Mon 9/21`. Weekly: eight 7-day windows ending today. Monthly: the last 6 calendar months. Weekly and monthly show the average per elapsed day, counting days with nothing logged as 0 |
| `GET /api/ai/status` | cur | `{configured, message (null or "AI isn't set up — an admin can set it up in Admin → App settings." / the Claude-needs-a-key text), provider, providerLabel, url (resolved), model, warmup (provider is ollama), last_ok (epoch or null), last_error, last_request {purpose, model, input_tokens, output_tokens, seconds, error, at}, privacy (null or {label, host})}`. Never the key |
| `POST /api/ai/warmup` | cur | Not set up → 503 with the message. Non-Ollama → `{ok:true, model, skipped:true}` and nothing is sent. Ollama → sends "hi"; `{ok:true, model}` or 502 with the error |
| `POST /api/ai/estimate` | cur | Body `{description}`, 1–2000 characters. Returns the parsed model JSON. Not set up → 503; provider errors and an answer without a JSON object → 502 with a readable message |
| `POST /api/ai/chat` | cur | Body `{message}`, 1–4000 characters. Returns `{reply}`. Not set up → 503; provider errors → 502 |
| `GET /api/admin/settings` | adm | The shared payload (`settings_core`): `{values:{ai_provider, ai_url, ai_model, ai_max_tokens, expose_daily_calories_sensor}, defaults:{…same keys}, meta:{key:{label, help, group, kind, restartRequired:false, min?, max?, choices?:[{value, label}], placeholder?, maxLength?, …}}, groups:[{id, label, help}] (ai, ha), secretsSet:{ai_api_key: bool}}` plus the app's `secrets:{ai_api_key:{saved, hint}}` and `providers:{ollama|openai|anthropic:{label, defaultUrl}}` — no key |
| `PUT /api/admin/settings` | adm | Body: any subset of the keys, `ai_api_key` (blank = keep) and `clear_ai_api_key` (bool). Returns the same shape as GET. 422 with a readable string `detail` (e.g. `"ai_url: must be an http:// or https:// URL…"`, `"Unknown setting(s): x"`, `"ai_url: Ollama needs its address…"`); nothing is stored then |
| `POST /api/admin/settings/test-ai` | adm | Optional body `{ai_provider?, ai_url?, ai_model?, ai_api_key?, clear_ai_api_key?}` — the form's unsaved values; a missing one = the saved value; a blank key = the saved key unless `clear_ai_api_key`; invalid → 422. Lists the provider's models (`GET /api/tags`, `/models` or `/v1/models?limit=100`, 10 s timeout, in a worker thread, no DB connection held). Returns `{ok, message, provider, label, url, model, models[≤100], modelCount, modelFound}` (`model` or `model:latest` listed). Generates and saves nothing |
| `GET /api/admin-storage-download-db` | adm | Download named `calorie-tracker-backup-YYYYmmdd-HHMMSS.db` (HA time), type `application/vnd.sqlite3`. The `ai_api_key` row is blanked (`""`) in the copy |
| `POST /api/admin-storage-import-db` | adm | Multipart field `file`. Returns `{status:"ok"}`; 400 with a specific message if the file is not valid. The restored App settings apply at once, except the access key: if the restored file has none, this install's key is put back (`settings.restore_secrets`). Also calls `ai_client.forget_state()` |
| `GET /…` | none | `StaticFiles(html=True)` mounted at `/`, registered last |

- Router order: `me.router, me.whoami_router, users, goals, weight, saved_foods, logs, ai, admin`, then the static mount.
- Security headers (`web_security`, §4): responses for `/` and `*.html` also get `Cache-Control: no-cache, no-store, must-revalidate` and `Pragma: no-cache`.
- Storage routes: the download is `db.backup_to_tempfile` sent by `backup_core.send_file` (deleted after the response) under `backup_core.file_name(...)`; the upload is received by `backup_core.receive` into a temp file in `DATA_DIR`, then validated and swapped in (§10).

## 7. Home Assistant & external integrations
**Daily-calories sensor** (`ha_sync.py`)
- Controlled by the App setting `expose_daily_calories_sensor` (§3a), read live by every push (`ha_sync.exposed()`).
- Pushes `POST http://supervisor/core/api/states/sensor.calorie_tracker_<slug>_daily_calories` with a Bearer `SUPERVISOR_TOKEN`. Body: `{state: round(today's total, 1), attributes: {unit_of_measurement: "kcal", friendly_name: "<name> Daily Calories", icon: "mdi:food-apple"}}`.
- Posts and removes go through `ha_sync.SENSORS`, a `sensor_publisher.Publisher` (`app/common/sensor_publisher.py`) whose post/delete hooks are the app's own `_post`/`_delete` (its HTTP calls through `app/common/ha_client.py` and its own log lines). Runs in the threadpool, 10 s timeout. A failure logs a WARNING and never fails the request. With no token, it logs one warning and skips.
- Triggered by food-log create or delete dated today (for the acting user's sensor) and by the periodic job (§8). Nothing is pushed while the setting is off.
- **Setting turned on** (`PUT /api/admin/settings` changed it; runs as a `BackgroundTask` right after the response): `push_all()` — one query for every user's total today (LEFT JOIN, so users with nothing logged get 0), the connection closed, then one POST per user.
- **Setting turned off**: `remove_all()` — `DELETE /api/states/<entity_id>` for every current user's slug plus every entity this process pushed successfully (`_published` = the publisher's memory, which covers a user renamed since). 2xx or 404 counts as removed. If any DELETE fails, `_removal_pending` is set and the periodic job retries until all succeed. Entities published under an old name before the last restart can't be known and are left to HA (REST-created states don't survive an HA restart anyway).
- No DB connection is ever held during an HA call.
- The slug comes from the display name: renaming a user creates a new entity, and two users with the same display name share one sensor.

**The household apps bus and the Household Assistant** (`app_messages.py`, `tools.py`; the shared `app_bus.py`,
`ha_ws.py`, `assist_tools.py`; APP_MESSAGES_SPEC §6.6, HOUSEHOLD_ASSISTANT_SPEC §4.2)
- `app_messages.start()` in the lifespan learns the sidebar page (`assist_tools.sidebar_page`: the Supervisor's
  `addons/self/info`, else `HOSTNAME`) into `config.INGRESS_PANEL` and starts the bus (its own WebSocket; the outbox
  thread only with a Supervisor token). The bus's tables (`bus_outbox`, `bus_seen`, `bus_apps`) are made by
  `app_bus` itself.
- One tool, `calorie.today` (`date?`, not in the future; default today): the asking person's own food log for that
  day — totals against their goals (the goals row, or the defaults) and each entry's meal, food, servings, kcal and
  macros. Never another person's day (`requested_by` only; the admin switcher's "acting as" never applies) and
  never weight. `requested_by` must have opened the app (a `users` row), else `nack not_allowed no_access`.
- Only while App settings → **Answer the Household Assistant** (`assistant_answers`, default on); otherwise `nack
  not_allowed off`. No per-person switch: the app has no per-person settings. The link opens `/foodlog/<date>`.
- **Sub-path links**: `/<page>/foodlog[/<date>]`, `/dashboard`, `/savedfoods`, `/weight`, `/goals` open that tab (and
  day) — `common/static/deeplink.js`, given the page by `/api/me`'s `page`; a request reaching the app itself is
  redirected to `#/…` (the shared `deeplinks.py`), which the page opens the same way.

**Time zone**
- At startup, `load_timezone()` (`ha_time.load(config.ZONE)`, `app/common/ha_time.py`) calls `GET http://supervisor/core/api/config` through the shared `ha_client`, reads `time_zone`, and sets `config.ZONE` (an `ha_time.Zone`; `config.set_timezone()` / `timezone_name()` / `now()` / `today()` are backed by it).
- If the call fails, the app stays on UTC. The zone is read once, so a change in HA needs an app restart.
- `config.now()`/`config.today()` are the only way to get the current time or date. They feed `/api/today`, the sensor total, the history, weight-history and month windows, and the backup filename. Never use `date.today()` or `datetime.now()`.

**AI** (`ai_client.py`) — the only code that talks to a model
- The requests, retries (429/500/502/503/504/529, Retry-After or 5/15/45 s), the 400 fall-backs, error mapping and model listing are the shared `app/common/ai_client.py` (`Client`, `Wording`); the app's `ai_client.py` keeps `Config`, `current()`, `generate()`, the warm-up, estimate/chat, its usage notes and its wording (`WORDING`, with the longer messages, and the key scrubbing).
- `current()` reads provider, resolved address, model, key and `ai_max_tokens` from App settings at each request. `generate(prompt, system=, want_json=, temperature=, timeout=, purpose=)` raises `AIError(kind)` with a readable message and records `last_request` (+ an INFO log line with tokens and seconds; never the key). Not set up → `AIError("not_configured")` before anything is sent.

| | Ollama | OpenAI-compatible | Anthropic Claude |
|---|---|---|---|
| Generate | `POST {url}/api/generate`, `stream:false`, `system` if given | `POST {url}/chat/completions`, `messages` [system?, user] | `POST {url}/v1/messages`, top-level `system`, `max_tokens` = `ai_max_tokens` |
| Auth | none | `Authorization: Bearer <key>` only when a key is set | `x-api-key`, `anthropic-version: 2023-06-01` |
| JSON mode | `format:"json"` | `response_format:{type:json_object}`; a 400 mentioning `response_format`/`temperature` → retried once without them | none: "Answer with the JSON object only." appended |
| Temperature (estimate 0.2) | `options.temperature` | `temperature` | `temperature` |
| Output limit | — | — | a 400 mentioning `max_tokens` → retried with the largest smaller number in the message, else 4096 |
| Models (Test connection) | `GET /api/tags` | `GET {url}/models` | `GET /v1/models?limit=100` (key required) |
| Tokens | `prompt_eval_count`, `eval_count`, `total_duration` | `usage.prompt_tokens/completion_tokens` | `usage.input_tokens` (+cache read/creation), `output_tokens` |

- **Retries**: 429, 500, 502, 503, 504, 529 up to 3 times, waiting `Retry-After` (1–60 s) or 5 / 15 / 45 s; `_sleep` is replaceable in tests. **Errors**: 401/403 "<provider> refused the access key (…). Check it on Admin → App settings."; 404 "… check the address and the model name …"; 429 "rate-limiting"; other retry statuses "overloaded or failing"; timeouts/unreachable name the provider and address. The provider's own message is included; the key is scrubbed from every message.
- **Warm-up (Ollama only)**: `ensure_warm()` runs before estimate and chat and sends "hi" (120 s timeout) only if the provider is Ollama, AI is configured, and nothing answered in the last `WARM_FOR_SECONDS = 240` (under Ollama's 5-minute keep-alive). A failed warm-up is kept as `last_error`, and the request is still attempted. Estimate and chat use a 300 s timeout.
- **Estimate**: prompt requires one JSON object `{food_name, serving_size, serving_unit, calories, protein, carbs, fat, note}`; the first `{` to last `}` of the answer is parsed and returned as-is (must be an object).
- **Chat**: system prompt "helpful, concise nutrition and fitness assistant…" plus the single message; no history and no user data. There is no rate limit and no one-at-a-time lock (requests run in the threadpool).
- **Privacy**: `privacy()` = `{label, host}` while `ai_is_external()`, else null; the frontend shows it to everyone (§9).

## 8. Background jobs
- `lifespan` runs `db.init_db()`, then `await ha_sync.load_timezone()`, then registers the `"ha_sync"` job with the shared runner (`app/common/housekeeping.py`, `Jobs().every("ha_sync", 900, ha_sync.periodic_sync, at_start=False)`) and starts it. On shutdown `jobs.stop()` cancels the task and awaits it.
- The `"ha_sync"` job runs every 900 s, first after 900 s, and calls `ha_sync.periodic_sync()`: while the setting is on, it pushes the sensor for **every** `users` row, disabled users included, using the slug of each name; while it's off, it only retries a pending removal. It repeats forever. Exceptions log a WARNING "Periodic HA sync failed: <error>" and the job continues. The first push comes 15 minutes after start. Purpose: the sensor rolls over to 0 after midnight even if nobody opens the app.
- Logging is set up by `housekeeping.setup_logging()` at the top of `main.py` (level INFO, format `"%(asctime)s %(levelname)s %(name)s: %(message)s"`). Use no `print()`.

## 9. Frontend
**Core rules**
- Shared browser helpers: `common/ui.js` (`window.UI`) loads before `app.js`; the app binds `api = UI.makeApi({…})`, `$`, `h = UI.h`, `escapeHtml = UI.escapeHtml`. Script order at the end of `index.html`: `common/ui.js`, `common/settings.js`, `common/people.js`, `common/backnav.js`, `common/whoami.js`, `app.js`.
- Every request uses a relative URL: `api()` strips a leading `/` because ingress serves the app under `/api/hassio_ingress/<token>/`. `api()` sends JSON by default (the fetch options are passed through as they are) and throws `Error(detail)` on any non-2xx response.
- `withUser(path)` appends `as_user=<actingUserId>` to every data call (goals, weight, saved foods, logs, summary, history). It is not added to `me`, `today`, `whoami`, `users`, `ai` or `admin` calls.
- Routing: the app is tab-based without URLs, except the Admin page, which uses the hash: `#/admin/settings`, `#/admin/users`, `#/admin/storage` (`#/admin` → settings); `#/users` and `#/storage` redirect (`history.replaceState`) to the matching Admin tab. Opening any other tab or the whoami page clears the hash. Admin navigation uses `BackNav.go()` (no history entries); Back (HA app gesture or browser) is handled by `common/backnav.js` (shared, loaded before `app.js`): closes the details popup, else returns to Food Log, else leaves the app.
- `escapeHtml()` (common/ui.js's `UI.escapeHtml`) escapes `&<>"'` and must wrap every piece of server or user text put into `innerHTML` or an attribute. Chat messages, the details modal, the "No admin yet" name and the AI notices use `textContent` / DOM nodes.
- **"No admin yet" banner** (`#noAdminBanner`, an empty container above the date bar, every page, everyone, filled by `HouseholdWhoami.fillNoAdminBanner` from `common/whoami.js`): "No admin yet — add your Home Assistant user name (**<username>**) to `admin_users` on the app's Configuration tab, save, and restart the app." plus a "How the app sees you" link button. Shown when `/api/me` says `noAdmin`.
- **AI notices** (`.ai-notice`, four places: Food Log Add Food card, Saved Foods form, AI Assistant card, App settings): from `/api/ai/status` — "AI isn't set up — an admin can set it up in Admin → App settings." while not configured (the ✨ buttons, chat input and Send are then disabled), and while `privacy` is set: "AI requests go to <label>. The food descriptions you send with ✨ Estimate with AI and the messages you type on AI Assistant are sent to <host> — outside your home network. That service's own privacy and data-retention rules apply. Nothing else from the app … is sent." (admins also get a "Change in Admin → App settings" button). Status is loaded at startup and refreshed when the AI tab or App settings opens and after each AI request or save.

**Startup**
- Sequence: `loadToday()` → `loadMe()` (also sets the "No admin yet" banner) → `routeFromHash()` (Admin deep links) → `loadUsers()` (admins only) → `refreshAll()` → `refreshAiStatus()`. After that, `loadToday()` runs every 5 minutes and on `visibilitychange`. When the day rolls over, it moves `state.date` and `state.summaryMonth` forward if they were showing today or the current month.
- If `/api/me` fails, the whole body is replaced with "Can't identify a Home Assistant user", the error message, and "Open Calorie Tracker from its panel in the Home Assistant sidebar."

**Layout**
- Sidebar (220 px): 🥗 brand, nav, user chip "Logged in as <name>" (a button that opens the whoami page), and the theme `<select>`.
- The sidebar collapses to a 68 px icon rail with the ‹/› button. The state is saved in localStorage `sidebarCollapsed` (through `HouseholdTheme.sidebarCollapsed()` / `setSidebarCollapsed()`).
- At ≤760 px the sidebar becomes a fixed bottom icon bar, and the brand, chip, theme select and collapse button are hidden.
- Top bar: a 👤 button (opens the whoami page) and `#userSelect`. The select is hidden for non-admins. It lists enabled users plus yourself, marked "(you)".
- **Date bar is shown on Food Log only** (`DATE_BAR_TABS = {"foodlog"}`; the whoami page hides it too). Controls: ‹ previous day; a pill showing e.g. "Sep 22" (", 2025" added when not the current year), a sub-label (Today / Yesterday / Tomorrow / weekday) and 📅; › next day; and Today.
  - The pill opens a hidden `<input type=date>` via `showPicker()`, falling back to `focus()`.
  - › is disabled once the date reaches today, and Today is disabled on today.
  - `shiftDate` does its date arithmetic in UTC so DST changes never skip or repeat a day.
  - Changing the date reloads the summary, the food log, the saved-food picker and the history.
- Themes `midnight` (default), `slate`, `daylight` and `auto` (Daylight when the device is light, else Midnight); `data-theme` on `<html>` always holds the resolved theme (never `auto`). The theme `<select>` is filled by `HouseholdTheme.bindSelect()`.
  - The shared surfaces, text and state colours come from `common/themes.css`; `style.css` adds the app's green accent and its own colours in the `:root, [data-theme="midnight"]`, `[data-theme="slate"]` and `[data-theme="daylight"]` blocks.
  - `common/theme-boot.js`, an external script in `<head>` placed before the stylesheets (`common/themes.css`, `common/settings.css`, `style.css`), applies the saved theme and sidebar state before first paint (localStorage `theme`, `sidebarCollapsed`). There is no inline script.
  - Charts read colors with `cssVar()`.

| Tab | Who | Contents / behavior |
|---|---|---|
| Dashboard | all | **Summary** card: calorie history bar chart with Daily/Weekly/Monthly pills. Bars use the accent color, or `--danger` when over goal, with a dashed goal line and a hint describing the window. **Monthly Breakdown** card: a month bar (‹, a picker over a hidden `type=month` input, and › disabled when `is_current \|\| is_future`), plus 5 tiles: per-day average vs goal for calories, P, C and F (red `.over` when above goal) and Avg Weight with the (±kg) change. Hint: "Averages over N of M days…" or "No data logged yet for this month." Opening the tab re-runs `loadHistory()`, because a canvas measures 0 width while its tab is hidden |
| Food Log | all | **The default tab.** **Add Food** card: meal select (breakfast/lunch/dinner/snack; snack by default), saved-food picker (fills the form with servings = 1), name, servings, calories/P/C/F, "✨ Estimate with AI", and "Add Food" (name and calories required). **Log** table: Meal, Food, Servings, Calories, P, C, F, ✕. **Day Summary**: 4 value/goal tiles plus Latest Weight. **Macros**: 3 progress bars, capped at 100% |
| Saved Foods | all | Form titled "Build a Repeated Food": name, serving size and unit, macros, notes textarea, AI estimate and Save. In edit mode (✎) the title reads `Editing "<name>"`, the button reads "Update Food", and a Cancel button appears. Table columns: Name, Serving, Calories, P, C, F, Details (a 60-character snippet plus ⤢, which opens `#detailsModal` with the full text from `detailsStore["sf-<id>"]`; closed by ✕, overlay click or Escape), and actions (＋ log, ✎, ✕ with a confirm). "＋ log" adds one serving as a snack **on the date-bar date** |
| Weight | all | Log form: date (defaults to the date-bar date at load), kg, note. Weight History line chart with the same pills, a dashed target line when `target_weight_kg` is set, and a line drawn only with ≥2 points. Entry list newest first, each with ✕ |
| Goals | all | 4 macro inputs plus starting and target weight (kg). Save sends all six fields: an empty macro is sent as 0, an empty weight as null |
| AI Assistant | all | Status dot (grey, `ok` or `err`), status text (not set up message / "Connected to <provider> · <model> at <url>." / "Set up for …" / last error), the last request's tokens and seconds, a "Wake up model" button (Ollama only), and the AI notice; the status refreshes when the tab opens. Chat log plus an input with Send or Enter. The "Estimate with AI" buttons use the name field, or `prompt()` if it is empty. The Saved Foods estimate also fills serving size and unit, and the status line shows the AI's note |
| 🛡️ Admin | admin | Last nav item, `hidden` in the HTML and un-hidden by `loadMe()` for admins only. `#tab-admin` holds a pill tab row (`.admin-tabs`, reusing `.hist-btn`, wraps on phones): **App settings \| Users \| Storage**. A non-admin who opens an Admin link sees only the card "Only admins can open this page." (`#adminDenied`). Sub-tabs below |
| ↳ App settings | admin | Drawn by `common/settings.js` (`SettingsPage.render`) from the payload's `meta` and `groups`: one card per group, a help line with the range and default under each setting, Save / Discard changes at the bottom ("N unsaved changes"), wrong numbers flagged at the field before saving. The app adds the AI notice at the top of the AI group, the Test connection block at its bottom, the footnote and its own messages. **🤖 AI**: Provider `<select>` (Not set up / Ollama / OpenAI-compatible / Anthropic Claude; changing it blanks the address, or brings back the saved one, and sets the placeholder to that provider's standard address), Address, Model, Access key (`type=password`, always empty; placeholder "Saved (…1234) — type a new one to replace it" or "Paste the key"; a **Remove** button only when one is saved, which sends `clear_ai_api_key: true` with the next save), Longest answer (tokens), each with its help text; **🔌 Test connection** posts the form (a blank key = saved key) to `test-ai` and shows ✓/✗ + the message inline. **🏠 Home Assistant**: "Publish daily calories to Home Assistant" switch with its help text. Footnote: changes apply right away; only `admin_users` is on the app's Configuration tab. **Save settings** PUTs the changed settings (the key only when typed, `clear_ai_api_key` after Remove); shows "✓ Saved. Changes apply right away." (plus the publishing/removing sentence when the switch changed) or the server's message, and refreshes the AI status. Editing any field clears the old Test connection result |
| ↳ Users | admin | Explanatory hint, then the shared people list (`common/people.js`, `PeoplePage.render`): one card per user with a "you" badge and an Enabled/Disabled switch (`PeoplePage.accessSwitch`) that saves immediately; on failure the switch reverts and an alert appears |
| ↳ Storage | admin | Download link `api/admin-storage-download-db`. Restore card: red warning, `.db` file input, `confirm()`, then a POST of `FormData` with `headers:{}` so the browser sets the multipart boundary. On success: "Import complete. Reloading…", then a reload after 1.2 s |
| How the app sees you | all | `#tab-whoami`, which has no nav button; opened from the user chip or 👤. See below |

**How the app sees you**
- Drawn by the shared `HouseholdWhoami.panel()` (`common/whoami.js`) from `/api/whoami`, inside the app's own card; rows and advice as in `WHOAMI_PAGE_SPEC.md` §3: User name (sent by Home Assistant) with a copy button (shows "not sent" if absent), User id with a copy button, Display name (not used for matching), Administrator in this app (Yes/No), and Names in the app's admin_users (the count). No extra rows.
- Advice, checked in this order:
  1. The user is an admin: "You are an administrator."
  2. `displayNameOnly`: replace the display name with the login name or id, then restart.
  3. `adminEntries == 0`: the list is empty in the running app, so restart.
  4. Otherwise: add the login name or id exactly as shown, then restart; case doesn't matter.
- There is no "Opened through Home Assistant" row: `viaIngress` is returned by the API but not shown. Copying uses `navigator.clipboard` and shows ✓ for a moment.

**Charts** (hand-rolled `<canvas>`, no library)
- Y-axis gridlines and labels come from `niceTicks` (steps of 1, 2 or 5 × 10ⁿ). When there are more than 10 points, every other x label is shown.
- The hover tooltip is a `<div>` inside `.chart-wrap`, positioned and clamped in JS so it never runs off the chart.
- ←/→ on a focused canvas steps through the points. The hovered bar gets an outline, and a hovered point grows from 3 px to 5 px radius.

**CSS gotcha**
- Keep `[hidden]{display:none!important}`. Without it, the button classes' `display` rules override the `hidden` attribute.

## 10. Invariants & pitfalls
- Never add `ports:` or host networking. The auth model rests on ingress being the only way in, plus the origin gate.
- Keep frontend URLs relative. Get the date from `/api/today` and never from the browser clock. On the server, use `config.today()`/`now()` only.
- The Alpine image needs `tzdata`. Without it every `ZoneInfo()` call fails and the day rolls over at UTC midnight.
- Admins are matched by user id or login name only. Acting-as is enforced in `get_acting_user`, not in the UI. Disabling a user only hides them from the switcher: an admin can still act as them, and their sensor still syncs.
- **Backup** uses `Connection.backup()` into a temp file (`db_core.snapshot_to_tempfile`), which `backup_core.send_file` deletes after the response. Never serve the WAL-mode file directly.
- **Backup never carries the AI access key**: `backup_to_tempfile(blank_settings=("ai_api_key",))` sets that row's value to `""` in the copy.
- **Restore**, in order:
  1. Stream the upload to a temp file in `DATA_DIR` (`backup_core.receive`). A temp file in `/tmp` makes `os.replace` fail with EXDEV.
  2. `validate_backup_file` (`db_core.validate_file`): the file must open as SQLite, `PRAGMA integrity_check` must return `ok`, and all 5 tables must be present; otherwise return 400.
  3. `db.import_from_tempfile` = `backup_core.restore_file` under `_import_lock`: `wal_checkpoint(TRUNCATE)`, `os.replace` over the DB, delete `-wal`/`-shm` (`db_core.swap_in`), then run `init_db()` so older backups get migrated.
  4. Remove the temp file in `finally`.
  5. Put this install's access key back if the restored file has none (`settings.restore_secrets`), then `ai_client.forget_state()`.
  Restore replaces everything else, with no merge and no undo.
- `escapeHtml` must escape quotes. Ingress pages run on HA's origin, so an XSS runs with the viewer's HA session.
- `food_logs.servings` is never multiplied into the numbers. The "＋ log" button and new weight entries use the date-bar date, not today.
- Known gap: numbers are not checked for range or finiteness. `allow_inf_nan=False` from `HA_ADDON_PATTERNS.md` §4 is not applied here yet.
- `python-multipart` is required by the import route (`File`/`UploadFile`).

## 11. Build, test & release
- `requirements.txt`: `fastapi==0.141.1`, `uvicorn==0.53.0`, `python-multipart==0.0.32`. `requirements-dev.txt`: `-r requirements.txt` plus `httpx2==2.13.1` (needed by Starlette's TestClient). All household apps use the same pins; bump them together.
- Tests: run `python3 -m unittest discover -s tests` from the app folder with the dev requirements installed.
  - `tests/_env.py` must be imported first. Built on `common_tests/env.py`, it sets a temp `DATA_DIR`, removes `SUPERVISOR_TOKEN`, and sets `DEV_ADMIN_USERS=adminy`.
  - The client is `TestClient(app, client=("127.0.0.1", 12345))` so requests pass the origin gate (`common_tests/ingress.py` has `ingress_client`, `identity_headers`, `user_headers` for the same).
  - `tests/common_tests/` (copies from the repository's `common/tests/`): the shared helpers above and `packaging_core.py`, `test_shared_copies.py` (no copy was edited), and the shared modules' own tests (`test_whoami_shared`, `test_auth_core`, `test_db_core`, `test_settings_core`, `test_web_security`, `test_backup_core`, `test_sensor_publisher`, `test_ai_client`).
  - `test_security_headers.py`: the CSP and `nosniff` on pages and API answers, and no inline scripts or `on…` handler attributes in the page.
  - `test_app.py`: ingress gate and 401; admin matching by id/login, not display name; `as_user` for non-admins ignored; cross-user edits 404; backup/restore round trip, junk files, older backups migrated; Ollama warm-up only when stale and never for cloud providers; AI input caps; time zone (UTC fallback); `escapeHtml` (under node, skipped without it); App settings shape/defaults (no personal defaults), admin-only, validation (422, nothing stored), partial updates, restore of the backup's settings; migration of `ollama_url`/`ollama_model` rows (on startup and on restore); "AI isn't set up" (503s, nothing sent, the app still works, the four notice places); "No admin yet" (API flag and banner); only `admin_users` read from options.json; the daily-calories sensor (on/off, publish-all, remove-all incl. renamed users, retries, no DB connection during HA calls); Admin APIs admin-only; sidebar layout.
  - `test_ai_providers.py` (the network replaced by a fake `ai_client._post`): request shape per provider, auth headers only when a key is set, JSON mode and its fallback, system prompts, Claude's `max_tokens` lowering, Claude needing a key, warm-up only for Ollama, settings applying to the next request; 401/404/unreachable/timeout messages (with the provider's message, never the key), retries with Retry-After and the cap; the key as a secret (never returned, blank keeps, replace, remove, never logged, blanked in the download, kept on restore); Test connection per provider (GET only, typed values, blank key = saved key, errors, 422 on a bad address); what counts as outside the home network and the privacy flag for everyone.
  - `test_packaging.py` (the checks come from `common_tests/packaging_core.py`, with the app's own values): `config.yaml` (version, url, ingress, `panel_admin: false`, no ports, options == schema == translations == `admin_users`, empty default), every `?v=` equals the version, README/DOCS/Dockerfile present, CHANGELOG.md starts with `# Changelog` and its newest (top) version heading equals the `config.yaml` version, `spec/` present and no `data model/`, `.dockerignore` entries, icon 128×128 and logo 250×100 (PNG header), no earlier release numbers in the docs, and a scan of every text file for personal details (only the repository URL allowed; only obvious example addresses).
- `.dockerignore`: `__pycache__ *.pyc .venv tests spec *.md !README.md icon.png logo.png translations requirements-dev.txt data *.db *.db-wal *.db-shm *.log .git`.
- **Every release**: bump `config.yaml` `version` (the Supervisor compares this string to offer an update), set every `?v=` in `index.html` to the same version, and add a `## <version>` section to `CHANGELOG.md` (Home Assistant shows it in the update dialog; it is read from the repository, so `.dockerignore` keeps it out of the image).
- Install: add the repository `https://github.com/sameerkotra/ha-apps` in **Settings → Apps → Install app → ⋮ (top right) → Repositories**, **Add**; then in **Settings → Apps → Install app** find the app, **Install**, set `admin_users` on the **Configuration** tab, **Start** (Information tab) (see `DOCS.md`).

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
- **Backups without secrets**: the download blanks the secret App settings (`ai_api_key`) in the copy (`settings.REGISTRY.scrub_secrets`); a restore keeps this install's value for each one the file leaves blank (`saved_secrets` before, `keep_secrets` after the migrations; a value the file carries is used).
