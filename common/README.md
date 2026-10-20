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

`python tools/sync_common.py --check` changes nothing and fails on any difference — including a stamped copy in
ANY top-level folder the manifest doesn't list (every folder that carries copies, `household_ai_docker` too,
must be in `manifest.json`, or its copies drift unseen);
`python tools/sync_common.py --adopt <app>/app/common/x.py` takes a copy that was edited by mistake back
into `common/` and copies it everywhere.

## Pages: themes and helpers

In `<head>`, before the app's own stylesheet: `<script src="common/theme-boot.js?v=…">` then
`<link rel="stylesheet" href="common/themes.css?v=…">`. The app's style.css sets its accent (`--accent`,
`--accent-dim`, `--accent-hover`, `--accent-contrast`, `--accent-soft`) and its own colours in three blocks,
`:root, [data-theme="midnight"]`, `[data-theme="slate"]` and `[data-theme="daylight"]`, each setting the same
variables. Theme menus: `HouseholdTheme.bindSelect(select)` (fills the options and keeps every menu in step; menus
are held weakly and forgotten once they have left the page, so one per dialog opening doesn't pile up;
`HouseholdTheme.unbindSelect(select)` lets one go explicitly).
`common/ui.js` loads before the app's scripts; its header lists every helper and option.

## Back navigation (backnav.js)

`BackNav.init({atHome, goHome, openLayers, closeLayer, isTab?, parentOf?})`. Back (the HA app's gesture or the
browser's) closes the top dialog, else returns to the previous in-app address, else to `parentOf(hash)` (when
given and not at home, e.g. a deep link to a person → `"#/people"`), else to the start page; only at home with
nothing left does it leave the app. One guard history entry as before; the trail of previous addresses lives in
memory and sessionStorage (`backnav.trail:<app folder>`, at most 20, consecutive duplicates collapsed).

- `BackNav.go("#/x")` remembers the current address; `BackNav.go(h, {replace: true})` / `BackNav.replace(h)`
  doesn't — use it for filter, sort, search and in-page tab changes.
- `isTab(hash)` → true for top-level tab addresses: going to one starts a new trail (Back retraces the tab, then home).
- `BackNav.go(h, {keepTrail: true})` (or `<a href="#/x" data-keep-trail>`): to a tab address without starting a
  new trail — a link from one tab into another (Calorie's "Edit goals"), so Back returns to it.
- `BackNav.go(h, {silent: true})` changes the address without a hashchange event; `BackNav.replace("")` clears the
  hash completely (the page's own URL, no trailing `#`).
- `BackNav.back()` for an in-app ← button (previous → parent → home; dialogs are not closed first; → true if it moved).
- `BackNav.peek()` → the address Back would return to from the trail (null when empty), so a header can name the
  real back target (Family Tree); `BackNav.clearTrail()` forgets the trail (Vault on lock).
- `parentOf(hash, url)`: `url` is the page's full relative URL (path + search + hash), for apps with several pages.
- `BackNav.goPage(url, {replace})` for apps with separate HTML pages (Receipt): remembers path + search + hash
  (`replace: true` → doesn't), takes the guard entry away first (Back over it), then `location.replace(url)`, so
  no page change leaves an entry in Home Assistant's history; Back to an entry on another page does the same. A
  page that loads with a non-empty trail arms the guard. Apps that pass none of the new options get previous-page
  Back as they are.
- Links to a place on the page (`<a href="#some-id">`, not `#/…`) just scroll there (a `<details>` opens) without
  changing the address; a `#x` link with no such element is navigation as before.

## Page states and shared CSS utilities

ui.js: `UI.loading(box, text = "Loading…")`, `UI.empty(title, text, action)` (action `{label, onclick, class}` or an
element), `UI.errorBox(err, retry, {retryLabel, retryClass})` (role=alert, a Retry button when `retry` is a
function), `UI.money(n, {symbol: "$", decimals: 2, plus, blank})`, `UI.toast(text, {action: {label, onclick, class,
id}})` (an Undo-style button; a click runs it and closes the toast).

Button classes: `UI.empty`'s action, `UI.errorBox`'s Retry and `UI.confirmDialog`'s OK/Cancel (`{danger: true}` →
the danger class) use `UI.buttonClasses` — default `{primary: "btn-primary", ghost: "btn-ghost", danger:
"btn-danger"}`. An app whose CSS names its buttons differently sets it once after ui.js loads (kinds left out keep
the default; each call can still pass its own class): Chat, Vault and Splitpot `"btn primary"` / `"btn ghost"` /
`"btn danger"`; Receipt `"btn btn-primary"` / `"btn btn-ghost"` / `"btn btn-danger"`; Calorie ghost
`"btn-secondary"`, danger `"btn-danger-outline"`. The others use the defaults.

Dialogs: `UI.openModal` adds `ui-modal-fallback` to its backdrop when the app's CSS doesn't make `.modal-backdrop`
`position: fixed`; themes.css then gives it a plain look (apps that style their dialogs are unchanged). With no
`#modalRoot` / `#toastRoot` on the page the dialog or toast goes into `<body>`. themes.css (after the themes; additive only): `--safe-bottom`, `--bottombar-h`;
`.ui-spinner`, `.ui-loading`, `.ui-empty`, `.ui-error`; `.summary-strip > .kpi > .kpi-label + .kpi-value`;
`.ui-group-head` with `.ui-group-total` (sticky group header; not `.group-head`, which Todo and Splitpot use for
other things; in a table use `<tr class="ui-group-head"><th colspan=…>…</th>[<td class="ui-group-total">]</tr>`: the
row stays a table row and its cells get the look); `.ui-toast-action` (zero specificity: app classes win); `.danger-zone`, `.btn-danger-outline`; `details.about > summary` (folded intro text); `.tap44` /
`.touch-target` (44×44px minimum on touch screens — opt-in, because a global `.icon-btn` rule would break the
dense chip buttons in Family Tree, Arcade and Chat).

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

## Request bodies, uploads and images

`web_security.BodyLimit` (an ASGI middleware, added before the app's own middleware) refuses a body over the route's limit with 413 — by
Content-Length before the route runs, and by counting a chunked body as it arrives; routes that need the
length up front refuse chunked bodies (411). `SecurityHeaders(..., static_immutable=True)` (opt-in: only for an
app whose pages link every asset with a `?v=` that changes on each release) lets browsers keep versioned static
assets for good (`Cache-Control: public, max-age=31536000, immutable`); the API is `no-store` by default
(`api_no_store="default"`: a route's own Cache-Control is kept). `image_guard.check_image`
reads an upload's header (format, width, height) without decoding it and `image_guard.decode_slot()` lets at
most two decodes run at once. `wildcard.match` matches `*`/`?`/`[..]` patterns without regular expressions
(a typed pattern can't hang a thread). `backup_core.check_members(..., max_total_bytes=…)` refuses a zip that
unpacks to more than the app accepts; `sandbox_run.Scratch.read()` / `.files()` read only regular files a tool
wrote; `db_core.MaintenanceGate` pauses background work during a restore.

## App messages: signing

`from` on the event bus is a claim: any app with `homeassistant_api: true` (or an HA admin) can fire a
`household_apps` event as any app — including as the Household Assistant, which lets it call every app's
assistant tools as any person. `app_bus.start(..., signing_key=…)` (a function reading the app's
`bus_signing_key` secret setting) makes every event carry an HMAC-SHA256 `sig`, and an app with a key refuses
anything unsigned (`nack unsigned`). Set the SAME key in every household app's Admin → App settings; without
one, everything works as before (unauthenticated). The Setting line each app adds is in app_bus's docstring.

## Who counts as the ingress proxy

`auth_core.from_ingress` / `refuse_outsiders` trust the Supervisor's ingress proxy (172.30.32.2). Loopback
(127.0.0.1, ::1) counts only outside Home Assistant (`auth_core.default_hosts()`: no `SUPERVISOR_TOKEN`, or no
`/data/options.json` — tests and development); inside a running app container another process (a PDF tool, a
hijacked library) could otherwise connect to 127.0.0.1 with forged identity headers. A health check from inside
the container must use a route that needs no identity.

## Security headers and inline scripts

Every app sends a Content-Security-Policy (its `CSP` string in `main.py`, passed to
`web_security.SecurityHeaders`). `script-src 'self'` everywhere: no inline `<script>` blocks, no `on…=`
attributes, no `javascript:` URLs, no `eval` (htmx: no `hx-on`, `js:` values or trigger filters). Values a
page script needs from the server go in `data-*` attributes (JSON with Jinja's `tojson`, in single quotes), read
through `document.currentScript.dataset` or from the element. Inline styles are allowed only where an app
still uses them (`style-src 'self' 'unsafe-inline'`); Arcade, Chat, Todo and Vault need none.
