# Receipt Price Intelligence: specification

How the app is built, for people changing it. What it does for its users is in [DOCS.md](../DOCS.md); the detailed
references are in [`docs/`](../docs): the API (`API.md`), the database (`DATABASE.md`), receipt reading
(`LLM_EXTRACTION.md`), recommendations and insights, the trip planner and security.

## 1. Shape

- A Home Assistant app reached only through ingress (`ingress_port` 8099, no published port).
- FastAPI + SQLAlchemy on one SQLite file (`/data/receipt_price_intelligence.db`); plain HTML pages in `app/static/`
  with one shared script (`app.js`, global `RPI`) and stylesheet (`app.css`). No build step, no external assets.
- Entry point: `python3 -m app.main` (the Dockerfile sets `DATABASE_PATH`, `IMAGE_PATH`, `HOST`, `PORT`).
- The version lives in `config.yaml` and `app/config.py` (`APP_VERSION`); the tests check they match. Pages are served
  by `main._page` with `?v=<version>` on their scripts and stylesheets (`app.css`, `app.js`, `common/backnav.js`,
  `common/whoami.js`, `common/theme-boot.js`, `common/themes.css`, `common/ui.js`, `pages/<page>.js`) and
  `Cache-Control: no-store`.
- **Shared code**: `app/common/` (Python: `whoami`, `ha_client`, `auth_core`, `settings_core`, `web_security`,
  `backup_core`, `sensor_publisher`, `geo`, `csv_export`), `app/static/common/` (`theme-boot.js`, `themes.css`,
  `ui.js`, `settings.js`, `settings.css`, `backnav.js`, `whoami.js`) and `tests/common_tests/` are copies of the
  repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see
  `common/README.md`). Never edit a copy; `tests/common_tests/test_shared_copies.py` fails if one was changed. No
  `db_core`: the app uses SQLAlchemy (`ensure_columns` through the inspector, engine-level pragmas).
- **Security headers** (`web_security.SecurityHeaders(CSP, csp_skip=is_pdf, pages_no_cache=False,
  other_no_store=True).install(app)`, policy `CSP` in `main.py`): `Content-Security-Policy: default-src 'self';
  script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:;
  connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'`
  and `X-Content-Type-Options: nosniff`. PDFs get no CSP (Chrome's viewer refuses a PDF whose own policy has
  `object-src 'none'`). Everything outside `/api/` is `no-store` unless the route set its own; the API's answers are
  left alone.

## 2. Identity and administrators (`app/auth.py`)

- `ingress_gate` (outermost middleware) refuses (403, its own text "Forbidden: open this app from Home Assistant",
  through `auth_core.refuse_outsiders`) every request whose TCP source is not in `auth_core.INGRESS_HOSTS` (the ingress
  proxy `172.30.32.2` or loopback), and every `/api/` request (401) without `X-Remote-User-Id`.
- The person is `X-Remote-User-Id`, with `X-Remote-User-Name` and `X-Remote-User-Display-Name`; a `users` row is kept
  for each (receipts and lists refer to it).
- Administrators: the `admin_users` option (a list, read from `/data/options.json` at start-up by `load_admin_users`
  with `auth_core.read_options` / `auth_core.admin_entries`), matched against the user id or login name without regard
  to case (`is_admin_identity`: `auth_core.is_admin` with `casefold`). Nothing else makes anyone an administrator. The `users.role` column
  only mirrors it (refreshed at start-up and on each visit).
- `GET /api/v1/me` (`is_admin`, `noAdmin`) and `GET /api/v1/whoami` (the page *How the app sees you*): the shared
  contract from `app/common/whoami.py` (`WHOAMI_PAGE_SPEC.md`): `haUserId`, `haUsername`, `haDisplayName`, `nameSent`,
  `isAdmin`, `displayNameOnly`, `adminEntries` (a count, never the list), `noAdmin`, `viaIngress`, `extras` (none).

## 3. App settings (`app/app_settings.py`, `app/config.py`)

- Table `app_settings(key PRIMARY KEY, value JSON, updated_at, updated_by)`; a key without a row uses its default.
- `app_settings.py` builds the shared registry (`settings_core.Registry`, `app/common/settings_core.py`) from its
  `FIELDS` (group, label, help) and `_SPEC` (default and limits) lists; the registry does the validation, storage,
  cache and GET/PUT payload, and the app keeps its hooks (change hooks, secrets, the snapshot below).
- `AppSettings` (`REGISTRY.model`: pydantic, `extra="forbid"`, strict, no NaN/inf) validates the merged result of every change; only
  changed keys are written. Messages name the setting by its label.
- `GET /api/admin/settings` → `{values, defaults, meta, groups, secretsSet}`; `PUT` takes only the keys that change
  and returns the same. `meta[key]` gives `group`, `label`, `help`, `kind` (`bool`, `int`, `float`, `text`, `choice`,
  `secret`), `choices`, `min`/`max`, `maxLength` (text fields) and `restartRequired` (always false). The App settings
  page in `admin.html` is drawn from it by the shared `common/settings.js` (one card per group, help, range and
  default under each setting, Save / Discard changes with the count of unsaved changes, wrong numbers flagged at
  the field before saving).
- Secrets (`model_api_key`, `imap_password`, `web_search_api_key`, `web_search_token`) are never sent; `""` clears one.
- `get_settings()` returns one read-only snapshot of the settings plus where things live, the same object until a
  setting changes; every caller reads it when it needs a value, so changes apply at once. Things built from settings
  are rebuilt when they change: the extraction client (model URL or timeout), the reading and web-search
  semaphores, the daily check (its hour is re-read every five minutes) and the log level (a change hook).
- Backups: exports contain the table. Restoring a file without settings keeps the current ones.

## 4. Pages and navigation (`app/static/`)

- Every page loads `static/common/theme-boot.js` and `static/common/themes.css` in `<head>` (the shared theme boot:
  applies `localStorage.theme` — `midnight`, `slate`, `daylight` or `auto` — as `data-theme`, and `data-sidebar`,
  before first paint; Auto = Daylight on a light device, else Midnight), then `static/app.css`; at the end of
  `<body>`, `static/common/backnav.js`, `static/common/ui.js`, `static/app.js` and the page's own script
  `static/pages/<page>.js` (`admin.html` also loads `common/settings.css` and `common/settings.js`; `whoami.html` loads `common/whoami.js`). No page has an
  inline `<script>` (CSP `script-src 'self'`): the page scripts are the files in `static/pages/`, and the version is
  baked in as `data-app-version` on `app.js`'s `<script>` tag (`app.js` reads it as `BAKED_VERSION`; `checkVersion`
  compares it with the server's).
- `app.js` builds on `common/ui.js` (`UI.makeApi` with the `BASE` URL and the parsed error body on `err.data`, `h()`)
  and wraps the page in the shell: a sidebar (sections, *Signed in as* → `whoami.html`, the theme menu
  Midnight/Slate/Daylight/Auto through `HouseholdTheme.bindSelect`, collapse) that becomes a bottom bar at 760 px and
  below, and the "No admin yet" banner. The banner names the user ("No admin yet — add your Home Assistant user name
  (<name>) to admin_users on the app's Configuration tab, save, and restart the app. [How the app sees you]") and is
  drawn by `common/whoami.js`, which `app.js` loads on demand only when `/api/v1/me` says `noAdmin`. Admin is shown
  to administrators only (the server enforces it).
- `whoami.html` draws *How the app sees you* with the shared `HouseholdWhoami.panel()` (`common/whoami.js`).
- Moving between pages replaces the history entry (`RPI.go`, and same-app links are routed through it), so with
  `BackNav` the back gesture goes: open dialog → List (the start page) → out of the app.
- Admin pages share `RPI.adminTabs()`: App settings and Homes and people (`admin.html`), Backup (`backup.html`), Web
  debug (`debug.html`).

## 5. Home Assistant, maps, exports and backups

- `services/ha.py` makes its Core API calls through `app/common/ha_client.py` (same `HAError` behaviour;
  `HA_API_URL` / `HA_API_TOKEN` still honoured).
- Sensors: the shopping-list sensor's "only when it changed, and at least every 15 minutes" memory is a
  `sensor_publisher.Publisher` (`app/common/sensor_publisher.py`); `alerts.publish_sensors` posts the home's
  sensors through another one (an `HAError` stops the run).
- Maps: the one-request-a-second throttle (`geo.Throttle`), the Nominatim search URL and first hit, OSRM coordinates
  and the HTTP fetch are `app/common/geo.py`; `services/geo.py` keeps the User-Agent, queries, retries, cache and
  errors, and `services/geo.user_agent()` also builds `osm.py`'s User-Agent.
- CSV (`receipts.csv`, `items.csv`): written by `app/common/csv_export.py` (UTF-8 BOM; a text cell starting with
  `=` `+` `-` `@`, tab or CR gets a leading `'`; numbers never), byte for byte as before.
- Backups: `services/backup.py` names the file with `backup_core.file_name`; `api/backup.py` sends it with
  `backup_core.send_file` and receives an upload with `backup_core.receive`; the safety copies are unchanged.

## 5a. The household apps bus and the Household Assistant (`app/app_messages.py`, `app/tools.py`)

- The shared `app_bus.py` (with `ha_ws.py`) starts in the lifespan (its own WebSocket; the outbox thread only with a
  Supervisor token). **Its tables are in a database of its own**, `app_bus.db` next to the app's: the bus wants plain
  sqlite3 and holds a write lock for the length of each answer, which would block the tools' own SQLAlchemy writes to
  the app's file (the HOUSEHOLD_ASSISTANT_SPEC §12 decision). Nothing in it needs backing up: the outbox, the ids
  already answered (7 days) and the apps heard from.
- It answers the **Household Assistant** (`assist.tools.list`, `assist.tool.call`; the shared `assist_tools.py`,
  APP_MESSAGES_SPEC §6.6). Each tool opens its own SQLAlchemy session inside `reqcache.scope()`. `requested_by` must
  have a `users` row (has opened the app), else `nack not_allowed no_access`. Every home is visible to everyone here,
  so a tool takes `home?` (by name, any case); with several homes and none named, `nack invalid home`.
  - `receipt.shopping_list`: `shoplist.get_list(online=False)` — unticked items `{item, qty, cheapest_at, price}`,
    grouped by cheapest store in the text.
  - `receipt.price` (`item`): the item by `shoplist._match`, else a name containing it; `analytics.compare_stores`
    over a year — `{store, latest, date, lowest, unit, trend}`, cheapest first.
  - `receipt.spending` (`month?`, `store?`): `analytics.summarize_spend` of the month (or the last 30 days), by store.
  - `receipt.shopping_list.add` (`acts`; `item`, `qty?`): only with `confirm: true`; `shoplist.add` (as the person),
    then the cheapest store.
- **Off by default** (App settings → Household Assistant → `assistant_answers`; `nack not_allowed off`). No
  per-person switch: the app has no per-person settings beyond notifications. Links: the sidebar page from
  `assist_tools.sidebar_page` (the pages have no sub-path routes).

## 6. Tests

`python3 -m pytest` (see `requirements-dev.txt`). `tests/test_app_settings.py` needs only pydantic;
`tests/test_packaging.py` checks the files (the shared checks from `common_tests/packaging_core.py`);
`tests/test_api.py` runs the gate and admin routes and needs the full requirements. `tests/common_tests/` holds the
copies of the shared tests (`test_shared_copies`, `test_whoami_shared`, `test_auth_core`, `test_settings_core`,
`test_web_security`, `test_backup_core`, `test_sensor_publisher`, `test_geo`, `test_csv_export`).

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: `app/main.py` starts uvicorn with `proxy_headers=False`, so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: `auth.ingress_gate` runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
- **Backups without secrets**: an export blanks the secret App settings (`model_api_key`, `imap_password`, `web_search_api_key`, `web_search_token`) in the copy (`settings.REGISTRY.scrub_secrets`); a restore keeps this install's value for each one the file leaves blank (`saved_secrets` before, `keep_secrets` after the migrations; a value the file carries is used). A safety copy is downloaded the same way (a blanked temporary copy); the copies kept on disk stay complete.
- **PDF tools** (`pdfinfo` and `pdftoppm` in `services/pdf.py`) run through `app/common/sandbox_run.py`: on a copy in a scratch folder, as the unprivileged `pdfworker` user made in the Dockerfile (no supplementary groups), with limits (1 GiB address space, 120 s CPU, 512 MB files, 64 open files, 64 processes, no core dumps) and the same timeouts as before; `lock_down` closes the data folder to other users at start-up. Without root (tests) only the limits apply.
- **Fetching pages** (`services/websearch.py`): `_check_url` resolves the host once, refuses any non-public address and returns the checked one; the opener's pinned HTTP/HTTPS connections connect to exactly that address (Host header, SNI and the certificate check keep the name), and every redirect is checked and pinned again.
- `/health/model` answers `{"configured": bool}` only; `/health/database` answers `{"status", "connected"}` without the error text (which is logged).
