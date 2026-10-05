# common/ — code shared by the household apps

Home Assistant builds each app from its own folder only, so apps can't import from here at run
time. Instead `tools/sync_common.py` copies these files into each app that uses them, and the
copies are committed with the app. **Edit files here, never the copies.**

| Folder | Copied to (in each app) | Imported as |
|---|---|---|
| `python/` | `app/common/` | `from .common import ha_notify` (`from ..common …` in `app/routers/`, `from app.common …` in tests) |
| `static/` | `app/static/common/` | `<script src="common/backnav.js?v=X.Y.Z">` (Receipt and Finance: `static/common/…`) |
| `tests/` | `tests/common_tests/` | `from common_tests.fake_ha import FakeHA` |

What is shared so far: the Home Assistant Core API client (`ha_client.py`; each app keeps a thin
`app/ha_client.py` that re-exports it and adds its own extras), Home Assistant's time zone and "today"
(`ha_time.py`), background jobs and the logging set-up (`housekeeping.py`), the event-bus WebSocket client
(`ha_ws.py`, Chat's notification replies), Home Assistant notify and people (`ha_notify.py`, `ha_people.py`), the ingress
identity and admin rules (`auth_core.py`: the ingress-proxy source check, the X-Remote-User-* headers, admin_users
loading and matching; each app keeps its own `auth.py` on top), SQLite helpers (`db_core.py`: connect with the app's
pragmas, the commit/rollback/close context manager, adding missing columns, backup snapshot, restore checks and the
file swap; each app keeps its own `db.py`, schema and MIGRATIONS list), the back gesture
(`backnav.js`), the page themes (`theme-boot.js` + `themes.css`: Midnight, Slate, Daylight and Auto, the
`theme` / `sidebarCollapsed` keys every app shares, old theme names mapped; each app's style.css adds its own
accent on top), the browser helpers (`ui.js`: `UI.h`, `UI.makeApi`, `UI.toast`, `UI.openModal`,
`UI.confirmDialog`, `UI.escapeHtml`, …; each app binds them to its usual names with its own settings),
"How the app sees you" and the "No admin yet" banner (`whoami.py`, `whoami.js`; see
`WHOAMI_PAGE_SPEC.md`), Admin → App settings (`settings_core.py`: each app lists its settings once and gets
validation, storage, the cache and the GET/PUT payload; `settings.js` draws the page from that payload's `meta`),
Admin → People (`people_admin.py`: phones from Home Assistant, the notify-services list, extra notify services,
the test limiter; `people.js`: the people list and the notify editor; both pages styled by `settings.css`),
Admin → Storage backup and restore (`backup_core.py`: the stamped file name, sending a temp file, building a zip
from the database snapshot and the app's extra files, receiving an upload with a size limit, opening and checking
a zip — safe member names plus the app's own checks — extracting members, and swapping the restored database in
and migrating it; each app keeps its routes, file names, zip layout, messages and status codes), the security
and caching headers (`web_security.py`: one `SecurityHeaders` per app — its Content-Security-Policy, `nosniff`,
Referrer-Policy and its Cache-Control rules — applied in the app's guard middleware or installed as one; and
`refuse_cross_site`, the 403 for a state-changing request a browser labels as coming from another site, in every
app's guard right after the ingress check), backups without secrets (`settings_core.Registry.scrub_secrets` /
`saved_secrets` / `keep_secrets` on `backup_core`'s `blank_settings` / `saved_settings` / `keep_settings`: a
downloaded backup never carries access keys or passwords, and a restore keeps the ones the install has), running
PDF tools on uploaded files as the unprivileged `pdfworker` user with resource limits (`sandbox_run.py`, Finance and
Receipt Price Intelligence), the
plain-HTTP warning banner (`http-warning.js`, with each app's own wording), the sensors an app publishes into Home
Assistant (`sensor_publisher.py`: POST/DELETE through `ha_client`, posting only what changed, the re-post timing —
Home Assistant forgets these states when it restarts — and the tick loop; each app keeps its entity ids, states,
attributes, intervals and switches), the AI providers (`ai_client.py`: Ollama, OpenAI-compatible and Anthropic Claude —
the requests, retries on 429/5xx, the 400 fall-backs, error mapping, listing models; each app keeps its Config,
settings, prompts, usage notes and wording), places on the map (`geo.py`: the 1-request-a-second throttle, the
Nominatim search URL and first hit, OSRM coordinates, the HTTP fetch; each app keeps its User-Agent, queries,
cache tables and error handling), CSV downloads (`csv_export.py`: the writer, the UTF-8 BOM and the formula guard
for text cells), and test helpers: `fake_ha.py`, `env.py` (for each app's `tests/_env.py`),
`ingress.py` (requests as Home Assistant's ingress proxy makes them) and `packaging_core.py` (the
packaging checks every app's `test_packaging.py` runs with its own values).

Messages between the apps over Home Assistant's event bus (`APP_MESSAGES_SPEC.md`): `python/app_bus.py` (with
`ha_ws.py`; `start(..., ws=)` shares an app's own WebSocket, `msg.after_commit()` runs steps after a handler's
transaction), `static/connected-apps.js` (Admin → Connected apps and links to another app's page,
`ConnectedApps.openAppPage`), and the test helper `tests/fake_ha_bus.py` with `tests/test_app_bus.py` — copied into
Household Chat and Household Todo (and the apps that use the bus later) and also run by the repository's
`tests/test_app_bus.py`. The browser files are also tested by the repository's `tests/test_common_static.py` (Node)
and `tests/test_common_pages.py` (the settings and people pages in Chromium).

Each copy starts with one header line ("Shared file: edit common/… and run tools/sync_common.py; don't edit
this copy.") with the SHA-256 of the body. `manifest.json` lists which app gets which file. Shared Python modules may use the app's own
`config` and `db` modules through `from .. import …`, and each other through `from . import …`
(`ha_notify`, `ha_people` and `ha_time` use the shared `ha_client`); each module's docstring says what
it needs from the app. A test that replaces a function of the app's `ha_client` (e.g. `request`) changes
only the app's own calls; to intercept the shared modules too, replace it in `app.common.ha_client` as well.

## Changing a shared file

1. Edit it here.
2. `python tools/sync_common.py` — copies it to every app that uses it and lists the apps that changed.
3. Run the tests of those apps (each has `tests/common_tests/test_shared_copies.py`, which fails if a copy
   was edited) and the repository tests (`python -m unittest discover -s tests`, which runs
   `sync_common.py --check`).
4. Bump the version of every app that changed and add a CHANGELOG line ("Shared code: …").

`python tools/sync_common.py --check` changes nothing and fails on any difference;
`python tools/sync_common.py --adopt <app>/app/common/x.py` takes a copy that was edited by mistake back
into `common/` and copies it everywhere.

## Pages: themes and helpers

In `<head>`, before the app's own stylesheet: `<script src="common/theme-boot.js?v=…">` then
`<link rel="stylesheet" href="common/themes.css?v=…">`. The app's style.css sets its accent (`--accent`,
`--accent-dim`, `--accent-hover`, `--accent-contrast`, `--accent-soft`) and its own colours in three blocks,
`:root, [data-theme="midnight"]`, `[data-theme="slate"]` and `[data-theme="daylight"]`, each setting the same
variables. Theme menus: `HouseholdTheme.bindSelect(select)` (fills the options and keeps every menu in step).
`common/ui.js` loads before the app's scripts; its header lists every helper and option.

## Admin pages: App settings and People

App settings: the app's `settings.py` builds a `settings_core.Registry` from a list of `Setting(key, default,
label, help=…, group=…, min=…, max=…, choices=…, secret=…, show_if=…, …)` and `Group(id, label, help)`; anything
special stays in the app as a callback (`validators=` on a setting, `check=` across settings, `prepare=` for
secrets, `on_write=` / `on_change=` hooks) or in its route (side effects such as re-publishing sensors, the
folder check before a folder change). A new setting needs only its `Setting(...)` line: the page draws it.
The page: `<link rel="stylesheet" href="common/settings.css?v=…">` after themes.css, `common/settings.js` after
ui.js, then `SettingsPage.render(box, {load, save, …})` — its header lists the hooks (custom controls, blocks above
or below a group, confirmations before saving, messages after).
People: `common/people.js` (`PeoplePage.render`, `notifyEditor`, `notifyDialog`, `phoneChips`, `accessSwitch`) with
the app's own columns and actions as hooks; the routes stay in each app (their paths and answers differ) and use
`people_admin.py` for the shared parts.

Design and plan: `SHARED_CODE_PLAN.md`.

## Build files and docs (not copied)

`build/requirements-base.txt` lists the shared pins (fastapi, uvicorn, python-multipart, …): an app that uses one
pins exactly that version. `build/Dockerfile.template` is the Dockerfile pattern. `python tools/check_build.py`
(also run by the repository tests, `tests/test_build.py`) checks every app's requirements, Dockerfile and
.dockerignore against them, including that the shared copies reach the image. `docs/DOCS_TEMPLATE.md` is the
layout of each app's README.md, DOCS.md and CHANGELOG.md.

## Security headers and inline scripts

Every app sends a Content-Security-Policy (its `CSP` string in `main.py`, passed to
`web_security.SecurityHeaders`). `script-src 'self'` everywhere: no inline `<script>` blocks, no `on…=`
attributes, no `javascript:` URLs, no `eval` (htmx: no `hx-on`, `js:` values or trigger filters). Values a
page script needs from the server go in `data-*` attributes (JSON with Jinja's `tojson`, in single quotes), read
through `document.currentScript.dataset` or from the element. Inline styles are allowed only where an app
still uses them (`style-src 'self' 'unsafe-inline'`); Arcade, Chat, Todo and Vault need none.
