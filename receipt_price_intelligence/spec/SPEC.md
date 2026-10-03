# Receipt Price Intelligence: specification

How the app is built, for people changing it. What it does for its users is in [DOCS.md](../DOCS.md); the detailed
references are in [`docs/`](../docs): the API (`API.md`), the database (`DATABASE.md`), receipt reading
(`LLM_EXTRACTION.md`), recommendations and insights, the trip planner and security.

## 1. Shape

- A Home Assistant app reached only through ingress (`ingress_port` 8099, no published port).
- FastAPI + SQLAlchemy on one SQLite file (`/data/receipt_price_intelligence.db`); plain HTML pages in `frontend/`
  with one shared script (`app.js`, global `RPI`) and stylesheet (`app.css`). No build step, no external assets.
- Entry point: `python3 -m app.main` (the Dockerfile sets `DATABASE_PATH`, `IMAGE_PATH`, `HOST`, `PORT`).
- The version lives in `config.yaml` and `app/config.py` (`APP_VERSION`); the tests check they match. Pages are served
  with `?v=<version>` on their scripts and stylesheet and `Cache-Control: no-store`.

## 2. Identity and administrators (`app/auth.py`)

- `ingress_gate` (outermost middleware) refuses (403) every request whose TCP source is not the ingress proxy
  (`172.30.32.2`) or loopback, and every `/api/` request (401) without `X-Remote-User-Id`.
- The person is `X-Remote-User-Id`, with `X-Remote-User-Name` and `X-Remote-User-Display-Name`; a `users` row is kept
  for each (receipts and lists refer to it).
- Administrators: the `admin_users` option (a list, read from `/data/options.json` at start-up), matched against the
  user id or login name without regard to case. Nothing else makes anyone an administrator. The `users.role` column
  only mirrors it (refreshed at start-up and on each visit).
- `GET /api/v1/me` (`is_admin`, `no_admin_yet`) and `GET /api/v1/whoami` (the page *How the app sees you*: the id and
  name sent, whether they matched, and a count of admin_users entries, never the list).

## 3. App settings (`app/app_settings.py`, `app/config.py`)

- Table `app_settings(key PRIMARY KEY, value JSON, updated_at, updated_by)`; a key without a row uses its default.
- `AppSettings` (pydantic, `extra="forbid"`, strict, no NaN/inf) validates the merged result of every change; only
  changed keys are written. Messages name the setting by its label.
- `GET /api/admin/settings` → `{values, defaults, meta, groups, secretsSet}`; `PUT` takes only the keys that change
  and returns the same. `meta[key]` gives `group`, `label`, `help`, `kind` (`bool`, `int`, `float`, `text`, `choice`,
  `secret`), `choices`, `min`/`max` and `restartRequired` (always false). The page is drawn from it.
- Secrets (`model_api_key`, `imap_password`, `web_search_api_key`, `web_search_token`) are never sent; `""` clears one.
- `get_settings()` returns one read-only snapshot of the settings plus where things live, the same object until a
  setting changes; every caller reads it when it needs a value, so changes apply at once. Things built from settings
  are rebuilt when they change: the extraction client (model URL or timeout), the reading and web-search
  semaphores, the daily check (its hour is re-read every five minutes) and the log level (a change hook).
- Backups: exports contain the table. Restoring a file without settings keeps the current ones.

## 4. Pages and navigation (`frontend/`)

- Every page has the theme bootstrap in `<head>` (validates `localStorage.theme` against
  `midnight`/`slate`/`daylight` and applies `data-theme` and `data-sidebar` before first paint) and loads
  `backnav.js` (shared with the other apps, byte-identical) before `app.js`.
- `app.js` wraps the page in the shell: a sidebar (sections, *Signed in as* → `whoami.html`, the theme menu, collapse)
  that becomes a bottom bar at 760 px and below, and the "No administrator yet" banner. Admin is shown to
  administrators only (the server enforces it).
- Moving between pages replaces the history entry (`RPI.go`, and same-app links are routed through it), so with
  `BackNav` the back gesture goes: open dialog → List (the start page) → out of the app.
- Admin pages share `RPI.adminTabs()`: App settings and Homes and people (`admin.html`), Backup (`backup.html`), Web
  debug (`debug.html`).

## 5. Tests

`python3 -m pytest` (see `requirements-dev.txt`). `tests/test_app_settings.py` needs only pydantic;
`tests/test_packaging.py` checks the files; `tests/test_api.py` runs the gate and admin routes and needs the full
requirements.
