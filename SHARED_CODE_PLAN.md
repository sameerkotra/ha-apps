# Shared code across the household apps — plan

Status: **option A chosen (2026-10-03). Phases 1–7 (and 4b) done, 2026-10-04.** Survey of the repository on
2026-10-03 (9 apps; Household Docs planned and paused). Sections 2–4 describe the tooling as built; §6 and §7 record
what each item became; §8 lists the decisions taken while building it, §9 what was deliberately not shared, and §10
the candidate fixes found on the way (behaviour changes, not made). No app version was bumped by the refactor itself.

## 1. Decision

Home Assistant builds each app **from its own folder only**, so apps can't import from a shared folder at run time.
**Option A:** one `common/` folder in the repository is the only place shared code is edited; a script copies it into
each app that uses it; the copies are committed; tests fail if any copy drifts.

Options turned down:

| Option | Why not |
|---|---|
| B. A shared Python package installed with pip from GitHub | Every build needs GitHub and a tag per change; JS/CSS/tests still need copying. |
| C. A shared base Docker image on `ghcr.io` | CI publishing for 3 architectures; installs depend on that registry; slow to change. |
| D. Merge everything into one big app | Loses per-app permissions (Vault has no `/share`, Finance is 64-bit only); one crash or update takes everything down. |

## 2. How the build works, and how apps refer to `common/`

### 2.1 What Home Assistant does on install or update
1. Downloads the repository (`https://github.com/sameerkotra/ha-apps`).
2. Finds each app by its folder's `config.yaml`.
3. Runs a Docker build with **that folder as the build context** and its `Dockerfile` — nothing outside the folder
   (`../common`, other apps) is visible.
4. Starts the container; at run time the app sees its image, `/data`, and `/share` if mapped.

So each app must **carry its own copy** of the shared files, committed in its folder.

### 2.2 Where the copies go
```
ha-apps/
  common/                         ← edit here, nowhere else
    python/  ha_notify.py  ha_people.py  auth_core.py  db_core.py …
    static/  backnav.js  ui.js  theme-boot.js  themes.css …
    tests/   fake_ha.py  env.py  ingress.py  packaging_core.py  test_*.py …
    build/   Dockerfile.template  requirements-base.txt      (patterns, not copied)
    docs/    DOCS_TEMPLATE.md                                 (pattern, not copied)
    manifest.json                 ← which app gets which files, and where
    README.md                     ← how sharing works, what each file does
  tools/sync_common.py            ← copies common/ into the apps
  tools/check_build.py            ← checks every app's build files against common/build/
  household_todo/
    app/common/                   __init__.py + Python copies
    app/static/common/            JS / CSS copies
    tests/common_tests/           test helper and shared-test copies
```
`manifest.json` gives each app its files and destination folders. As built, **every app uses the same three
destinations** — Receipt Price Intelligence's pages moved from `frontend/` to `app/static/`, Splitpot's `main.py` and
`public/` to `app/main.py` and `app/static/`, and Finance's spec from `data model/` to `spec/`:

| App | Python → | Browser files → | Tests → |
|---|---|---|---|
| All 9 | `app/common/` | `app/static/common/` (Finance: only the themes and `ui.js`) | `tests/common_tests/` |

### 2.3 How app code refers to them
- **Python** imports from its own folder: `from .common import ha_notify`, `from ..common import whoami` in
  `app/routers/` (Splitpot too, now that it is `app/main.py`; Receipt: `from app.common import …`).
- **What differs per app stays in the app's own file, built on the shared one** — e.g. Vault's `app/auth.py` uses
  `auth_core` and adds its sessions; Todo's adds "acting as". The shared files themselves never get app-specific
  edits.
- **Browser files** load relative to the page, like the app's other files (works through ingress):
  `<script src="common/ui.js?v=X.Y.Z"></script>` (Receipt and Finance: `static/common/…`).
- **Tests:** `from common_tests.fake_ha import FakeHA`, `from common_tests.ingress import ingress_client`,
  `from common_tests import packaging_core as pk` (the folder is `common_tests`, not `common`, so it can't shadow
  the app's `app.common` package on the test path).

### 2.4 Build files
- Dockerfiles that already `COPY app …` pick up `app/common/` with no change; Splitpot's now `COPY app ./app` too.
  The pattern is `common/build/Dockerfile.template`; `python tools/check_build.py` (run by the repository tests,
  `tests/test_build.py`) checks every app's Dockerfile, pins and `.dockerignore`, including that the shared copies
  reach the image.
- `.dockerignore` must not exclude `common/`; `tests/` (with `tests/common/`) stays out of the image as now.

## 3. Making a change to shared code
1. Edit the file in `common/`.
2. `python tools/sync_common.py` — copies it to every app listed in `manifest.json`, writes line 1 of each copy:
   `# Shared file: edit common/python/ha_client.py and run tools/sync_common.py; don't edit this copy. sha256=…`
   (`//` for JS, `/* */` for CSS), and prints **which apps changed**.
3. Run the tests: the repository drift test (§4) and each changed app's own tests.
4. **Bump the version of every app that changed** (each delivered update gets its own version), CHANGELOG line
   "Shared code: …", and the `?v=` strings if browser files changed.
5. Commit `common/` and the copies together; push. Each household's Home Assistant offers those apps' updates and
   rebuilds each from its own folder.

Other commands: `sync_common.py --check` (changes nothing; fails on any drift) and `sync_common.py --adopt <app file>`
(a fix made in an app's copy by mistake is taken back into `common/`, then copied everywhere).

## 4. Guards against drift
- **Repository test** (`tests/test_repo.py` runs `sync_common.py --check`): every copy equals `common/` byte for
  byte (after the header line) and every app has what `manifest.json` says.
- **Each app's own test** (`tests/common_tests/test_shared_copies.py`) checks its copies against the hash on line 1 —
  so an app folder tested or installed on its own still catches an edited copy.
- **Line endings:** `.gitattributes` forces LF (`* text=auto eol=lf`, binaries untouched), so Git on Windows can't
  turn copies into CRLF and make them differ.
- A copied file with no header, or a header pointing at a file that isn't in `common/`, fails the test.

## 5. Not shared on purpose
- Each app's **database** stays private in its own `/data`. People are synced from HA by each app with the same code;
  access switches stay per app (Vault off by default, Docs on).
- App features (lists, checklists, shared folders) aren't merged.
- Vault's sessions, CSP and crypto (stricter on purpose); Finance's page templates; Arcade's games.

## 6. What gets shared (catalogue)

### 6.0 Files with the same name today

| File / feature | Copies | State |
|---|---|---|
| `ha_notify.py` (phones + extra notify services) | 5 | ✅ identical |
| `ha_people.py` (HA persons, logins, phones) | 5 | ✅ identical |
| `backnav.js` (back gesture) | 7 | ✅ identical |
| `ha_client.py` (Supervisor REST: states, services, template, config) | 5 | ❌ 5 versions, 40–71% alike |
| `auth.py` (ingress source check, `X-Remote-User-*`, admin check, no-admin flag) | 8 | ❌ 8 versions, 12–39% alike |
| `settings.py` (App settings table, validation, cache, `GET/PUT /api/admin/settings`) | 7 | ❌ 7 versions |
| `config.py` (options.json → admins, paths, HA time zone) | 7 | ❌ 7 versions |
| `theme-boot.js` + theme variables in `style.css` | 4 + 8 | ❌ 3 boot versions; theme names differ (Vault/Midnight/…) |
| `housekeeping.py` (background loop in `lifespan`) | 4 | ❌ 4 versions |
| `ai_client.py` (Ollama / OpenAI-compatible / Claude) | 3 (+ Receipt's own `receipt_llm.py`/`textllm.py`) | ❌ ~50% alike |
| `ai_usage.py` (AI usage page and limits) | 2 | ❌ 15% alike |
| `geocode.py` | 2 | ❌ 22% alike |
| `ha_sensors.py` (publish `sensor.*` via `POST /api/states`, re-post every 5 min) | 2 (+ Vault guest Wi-Fi, Receipt `ha.py`) | ❌ |
| Notifications above `ha_notify` (`notify.py`, `notifier.py`, `alerts.py`) | 4 | ❌ each app's own |
| Admin backup / restore (DB snapshot zip, validate, swap, migrate) | 7 | ❌ each app's own |
| "How the app sees you" page (`WHOAMI_PAGE_SPEC.md`) | most | ❌ each app's own |
| `/share` path safety (`realpath` checks, hidden/symlink rules) | Chat (+ Docs planned) | — |


### 6.1 Already identical — move as they are

**Done (phase 1).** All four are in `common/` (`python/ha_notify.py`, `python/ha_people.py`, `static/backnav.js`,
`tests/fake_ha.py`) and copied to the apps listed.
| Item | Apps |
|---|---|
| `ha_notify.py` — phones + extra notify services, delivery | Family Tree, Arcade, Chat, Todo, Vault |
| `ha_people.py` — HA persons, logins, phones | Family Tree, Arcade, Chat, Todo, Vault |
| `backnav.js` — back gesture in the HA phone app | all except Finance, Chat |
| `tests/fake_ha.py` — a fake Home Assistant for tests | Family Tree, Arcade, Todo, Vault |

### 6.2 Same job, small changes
| # | Item | Apps today | What changes |
|---|---|---|---|
| 1 | **"How the app sees you" page** + `/api/whoami` | all 9 (each different) | One contract: fixed field names (`haUsername`, `haUserId`, `haDisplayName`, `isAdmin`, `adminEntries`, `noAdmin`, `viaIngress`, `notifyLinked`) + an `extras` list for app rows (counts and statuses only). Today the no-admin flag has three names (`noAdmin` / `noAdmins` / `noAdminYet`) and the user id two (`haUserId` / `userId`). `WHOAMI_PAGE_SPEC.md` rewritten from Finance's Jinja version to this. **Done (phase 3).** `common/python/whoami.py` + `static/whoami.js`; `noAdmin` everywhere; all 9 apps. |
| 2 | **"No admin yet" banner** | 8 | Same text, same placement, driven by `noAdmin`. **Done (phase 3).** One text everywhere (Finance adds the AI step); drawn by `whoami.js` (Finance: `base.html`). |
| 3 | **Ingress identity + admin check** (`auth_core`) | all 9 | One `get_user` / `require_admin`: source address check (`172.30.32.2` / loopback), `X-Remote-User-*` headers, case-insensitive match on id or login name. Apps keep their own extra rules (Vault sessions, Todo "acting as", Finance "view as"). **Done (phase 4).** `common/python/auth_core.py`, all 9; each app's `auth.py` (Splitpot `main.py`, Finance `auth.py` + `security.py`) is a thin layer with the same names and messages. |
| 4 | **Security headers** (CSP, `nosniff`, `Cache-Control: no-store` on `/api`) | `no-store` all; CSP/`nosniff` in 5 | One middleware with a per-app CSP. Apps with inline scripts (Calorie Tracker, Splitpot, Finance, Receipt) move them to files first — **medium** for those four. **Done (phase 6).** `common/python/web_security.py`, all 9. Correction: CSP/`nosniff` were in **4** apps (Family Tree, Arcade, Chat, Vault), not 5; now all 9 (new in Calorie Tracker, Todo, Splitpot, Finance, Receipt). Inline scripts moved out where there were any (Receipt, Finance). |
| 5 | **Themes**: `theme-boot.js`, theme variables, the `theme` / `sidebarCollapsed` keys | all 9 | One boot script, one set of theme names (Midnight, Slate, Daylight, Auto) with old names mapped; each app keeps its accent colour. **Done (phase 5).** `common/static/theme-boot.js` + `themes.css`, all 9; Auto everywhere; old names mapped (heritage/ink/vault/paper → Midnight, parchment/sandstone → Daylight). |
| 6 | **HTTP warning banner** (plain `http://` on the LAN) | 5 | Same banner in every app that takes anything sensitive; optional elsewhere. **Done (phase 6), Vault only.** `common/static/http-warning.js`. Correction: only **Vault** showed the banner, not 5 apps. Not added elsewhere (it would be a visible change); candidates: Finance, Chat, Family Tree, and Household Docs, whose spec already plans one. |
| 7 | **App settings framework** (`app_settings` table, validation, cache, `GET/PUT /api/admin/settings` → `{values, defaults, meta}`) | 7 | Apps declare their settings as a list; the **Admin → App settings page is drawn from `meta`** by one shared `settings.js` (groups, labels, help, ranges), so new settings need no page code. **Done (phase 5).** `common/python/settings_core.py` + `static/settings.js` / `settings.css`, all but Finance. Payload `{values, defaults, meta, groups, secretsSet?, …}`. |
| 8 | **Admin → People** (HA persons, access switch, phones from HA, extra notify services, *Send a test*) | 5 (+ simpler lists in others) | One page and routes; apps add columns (Vault set-up status, Docs folder). **Done (phase 5).** `common/python/people_admin.py` (Family Tree, Arcade, Chat, Todo, Vault) + `static/people.js` (those 5, Calorie Tracker, Splitpot). The notify editor's button is "Send a test" everywhere. |
| 9 | **Admin backup / restore** (`Connection.backup()` snapshot, zip, validate, `integrity_check`, swap, re-run migrations) | all 9 | One `backup_core` with hooks for extra files (Vault's vault files, Family Tree photos). Same file name pattern and admin card. **Done (phase 6).** `common/python/backup_core.py`, all 9; every app keeps its routes, file names, zip layout, messages and status codes (a backup from before restores into the new code in all 9). The admin card is **not** shared (see §9). |
| 10 | **Database helpers**: connect (WAL, foreign keys, busy timeout), `MIGRATIONS` runner (add missing columns at start-up and after restore) | 7 | One `db_core`; each app keeps its own schema. **Done (phase 4).** `common/python/db_core.py`, all but Receipt (SQLAlchemy); each app keeps its schema and a `MIGRATIONS` list (Arcade and Finance keep their numbered migrations). |
| 11 | **Start-up and background loop** (`lifespan`, `logging.basicConfig`, a housekeeping loop cancelled on shutdown) | all 9 / 4 | One `housekeeping` runner that apps register jobs with. **Done (phase 4).** `common/python/housekeeping.py` (`Jobs`, `periodic`, `setup_logging`), all but Finance and Receipt. |
| 12 | **`ha_client.py`** + **HA time zone and currency** (`today()` / `now()` in HA's zone) | 5 + others with their own | One client; one time helper (fixes apps still on UTC days). **Done (phase 4).** `common/python/ha_client.py` (all but Finance), `ha_time.py` (all but Finance, which sets `TZ` in `bootstrap.py`); each app's `ha_client.py` is a thin re-export plus its extras. Chat also got `ha_ws.py`. Vault and Receipt joined `ha_time` on 2026-10-07 (§10). |
| 13 | **HA sensors publisher** (`POST /api/states`, re-post every 5 min because HA forgets them on restart, remove on off) | Calorie Tracker, Arcade, Todo, Receipt, Splitpot, Vault (guest Wi-Fi) | One `ha_sensors` with the re-post loop; apps describe their sensors. **Done (phase 7)** as `common/python/sensor_publisher.py` (not `ha_sensors`: each app keeps its own `ha_sensors.py`): Calorie Tracker, Arcade, Todo, Receipt, Splitpot. Vault's guest Wi-Fi sensor unchanged (§9). |
| 14 | **Frontend helpers** (`ui.js`): `h()` DOM builder, `api()` fetch wrapper (ingress-relative paths, errors → toast), `toast()`, confirm dialog, date/number formatting in HA's zone | 7–8 (each own) | One file; `escapeHtml` string-building in Calorie Tracker, Splitpot and Finance replaced by `h()` (**medium**, also closes an XSS class). **Done (phase 5)** as `common/static/ui.js` (`window.UI`), all 9. The `escapeHtml` → `h()` rewrite of Calorie Tracker, Splitpot and Finance was **not** done (§9); their `escapeHtml` is now `UI.escapeHtml` (all five characters). |
| 15 | **AI client + AI usage page and monthly limits** (Ollama / OpenAI-compatible / Claude, URL + access key) | Calorie Tracker, Finance, Arcade, Receipt | One `ai_client` + `ai_usage`; Receipt's own receipt/vision calls move onto it later (**medium**). **Done (phase 7), client only.** `common/python/ai_client.py` (Calorie Tracker, Finance, Arcade). `ai_usage` not shared and Receipt's `receipt_llm.py` / `textllm.py` not moved (§9). |
| 16 | **Places: geocode + drive time** (Nominatim, OSRM, caching, polite rate limits) | Family Tree, Todo, Receipt | One `geo.py` with the cache and rate limit. **Done (phase 7).** `common/python/geo.py` (Family Tree, Todo, Receipt): throttle, Nominatim search URL / first hit, OSRM coordinates, the fetch; each app keeps its User-Agent, queries, retries, caches and errors. |
| 17 | **CSV export** (streamed, UTF-8 BOM for Excel, **formula-injection guard** — cells starting `= + - @` prefixed) | Finance, Todo, Receipt, Splitpot | One helper; the guard is new for most. **Done (phase 7).** `common/python/csv_export.py` (Finance, Todo, Receipt, Splitpot). New guard cells: Todo's maintenance history (Item, Category, Done by, Note, Files) and Splitpot's "<name> share" headers; Finance and Receipt byte for byte unchanged. |
| 18 | **Test helpers**: `_env.py`, `base.py` (TestClient from the ingress address with user headers), and the common half of `test_packaging.py` (config.yaml rules, CHANGELOG top version = config version, icons, `?v=` cache-busting, no personal text) | all 9 | Shared base classes; each app's packaging test keeps only its own rules. **Done (phase 2)** as `common/tests/env.py` (for each app's `_env.py`), `ingress.py` (`identity_headers`, `user_headers`, `ingress_client`) and `packaging_core.py`, copied to `tests/common_tests/` in all 9. |
| 19 | **Build files**: Dockerfile pattern (alpine + tzdata, non-root where possible), `.dockerignore`, pinned `requirements.txt` | all 9 (9 different Dockerfiles) | One template and one pin list (`common/requirements-base.txt`); apps add their own lines. **Done (phase 7).** `common/build/Dockerfile.template`, `common/build/requirements-base.txt`, `tools/check_build.py` (+ root `tests/test_build.py`); not copied. Only change to an app: Splitpot's Dockerfile `LABEL` before `EXPOSE` (order only). |
| 20 | **Docs templates**: README skeleton (unofficial warning, "built with Claude"), DOCS.md section order (setup → settings → what's sent where → backups → troubleshooting), CHANGELOG style | all 9 | Spec-level only: a `DOCS_TEMPLATE.md`; nothing copied. **Done (phase 7).** `common/docs/DOCS_TEMPLATE.md`; no app doc was rewritten for it (they follow it when next rewritten). |

### 6.2b New shared module (no app uses it yet) — **built (phase 4b)**
| Item | Apps | Note |
|---|---|---|
| **`app_bus.py`** — messages between apps over Home Assistant's event bus (`household_apps` events, outbox + ack, de-duplication, `hello`/availability) | none yet; first users Arcade + Chat (planning a duel) | Spec: `APP_MESSAGES_SPEC.md`. Built and tested in `common/` in phase 4b; copied into an app only when that app starts using it. Reuses Chat's WebSocket client (`ha_events.py`) and `ha_client.py`. |

### 6.3 Shareable later (medium, when an app needs it)
| Item | Apps | Note |
|---|---|---|
| `share_paths.py` — `/share` path safety | Chat (+ Docs) | When Docs is built. |
| Audit log helper (`audit(action, …)`, never content) | Vault, Chat, Receipt | Same table shape; apps choose actions. |
| Live updates (Server-Sent Events) | Chat, Receipt | Only if another app needs live updates. |
| Print stylesheet + print page pattern | Family Tree, Vault | Docs will need it too. |
| Folder/zip download (streamed, size-checked) | Family Tree, Chat, Vault | Same streaming code. |
| Reminders with notifications (stages, quiet hours) | Family Tree, Todo, Vault, Finance, Arcade | The **sending** part is shared already (`ha_notify`); the scheduling rules differ per app — share only a small "send once per stage" helper. |
| Children / limits | Arcade (Calorie Tracker, Family Tree kid mode) | Different meanings per app; keep separate unless one design emerges. |

### 6.4 Keep separate
Each app's database and domain logic; Vault's sessions, CSP and crypto (stricter on purpose); Finance's page
templates; Arcade's games.

## 7. Order of work

Each phase: a short spec (what the shared file does, what each app changes) → your go-ahead → build → the apps that
changed get a version bump. Phase 1 changes no behaviour.

| Phase | What | Apps touched | Version bumps |
|---|---|---|---|
| **1. Tooling** | `common/`, `manifest.yaml`, `sync_common.py` (`--check`, `--adopt`), the drift test, `.gitattributes`; move in the already-identical files (§6.1: `ha_notify.py`, `ha_people.py`, `backnav.js`, `fake_ha.py`). | Files stay at their current paths and get **no header line yet** (the manifest points at them; the drift test compares whole files), so no app's contents change. Headers and the move to `common/` folders come with phase 4. | **None** |
| **2. Test helpers** | `_env.py`, `base_test.py`, the common half of `test_packaging.py` (§6.2 #18). | All 9 (tests only — not in the image) | None (tests aren't shipped) |
| **3. "How the app sees you" + "No admin yet"** | Rewrite `WHOAMI_PAGE_SPEC.md` as the shared contract; one page script and server helper (§6.2 #1–2). | All 9 | Patch each |
| **4. Foundations** | `auth_core`, `config_core`, `db_core` (connect + migrations), `housekeeping`, `ha_client` + HA time zone/currency (§6.2 #3, #10–12). Imports move to `app/common/`. | All 9 (Finance/Splitpot/Receipt backend parts) | Patch each |
| **4b. App bus** | `common/python/app_bus.py` + its shared tests and the fake HA event bus (`APP_MESSAGES_SPEC.md` §9). Chat's `ha_events.py` WebSocket client generalised into it. Built and tested in `common/` only. | **None** — not copied into any app until a feature uses it (each use: own spec → go-ahead → the apps involved get it and a version bump). | None |
| **5. Look and admin pages** | Themes (one set of names, old ones mapped), `ui.js` helpers, App settings page drawn from `meta`, Admin → People (§6.2 #5, #7, #8, #14). | 8 (Finance partly) | Minor each |
| **6. Safety** | `backup_core`, security headers middleware (inline scripts moved out first in Calorie Tracker, Splitpot, Finance, Receipt), HTTP warning banner (§6.2 #4, #6, #9). | All 9 | Minor each |
| **7. Features** | HA sensors publisher, AI client + usage limits, places + drive time, CSV export with the formula guard, Dockerfile template + pinned requirements, docs template (§6.2 #13, #15–17, #19–20). | Per feature (see §6.2) | Patch/minor where changed |
| **Later** | §6.3 items when a second app needs them (`share_paths.py` with Household Docs). | — | — |

**Status, 2026-10-04: phases 1–7 and 4b are done.** No version was bumped and no CHANGELOG was written by the
refactor itself (the releases that carry it bump them). Apps changed per phase:

| Phase | Done | Apps changed |
|---|---|---|
| 1. Tooling | ✅ 2026-10-04 | none in content (`common/`, `manifest.json`, `sync_common.py`, the drift tests) |
| 2. Test helpers | ✅ 2026-10-04 | all 9 (tests only) |
| 3. "How the app sees you" + "No admin yet" | ✅ 2026-10-04 | all 9 |
| 4. Foundations (`ha_client`, `ha_time`, `housekeeping`, `auth_core`, `db_core`) | ✅ 2026-10-04 | all 9 (`ha_client` / `ha_time` / `housekeeping`: all except Finance); no `config_core` (§8) |
| 4b. App bus | ✅ 2026-10-04 | none (built and tested in `common/` only; Chat's `ha_events.py` runs on the shared `ha_ws.py`) |
| 5. Look and admin pages (themes, `ui.js`, App settings, People) | ✅ 2026-10-04 | all 9 (App settings and People: all except Finance) |
| 6. Safety (`backup_core`, security headers, HTTP warning) | ✅ 2026-10-04 | all 9 |
| 7. Features (sensors, AI client, places, CSV, build files, docs template) | ✅ 2026-10-04 | Calorie Tracker, Family Tree, Finance, Arcade, Todo, Receipt, Splitpot (Chat and Vault unchanged) |

## 8. Decisions taken while building it

- **`common/manifest.json`, not `manifest.yaml`:** JSON is read with the standard library, so `sync_common.py`,
  `check_build.py` and every app's tests need no PyYAML.
- **Test copies go to `tests/common_tests/`**, not `tests/common/` (a `common` package on the test path would shadow
  the app's `app.common`). The helpers are `env.py` (for each app's `_env.py`), `ingress.py` (`identity_headers`,
  `user_headers`, `ingress_client`, in place of a `base_test.py` class) and `packaging_core.py` (the checks each app's
  `test_packaging.py` runs with its own values); plus `fake_ha.py` and each shared module's own tests.
- **One layout for every app:** `app/common/`, `app/static/common/`, `tests/common_tests/` (§2.2). Splitpot became an
  `app/` package (`app/main.py`, `app/static/`), Receipt's pages moved to `app/static/`, Finance's spec to `spec/`.
  Family Tree's own `app/common.py` was renamed `app/models.py` to free the name.
- **Names:** the sensors helper is `sensor_publisher.py` (each app keeps its own `ha_sensors.py` built on it); the
  WebSocket client is `ha_ws.py`; the page helpers are one `ui.js` (`window.UI`); the theme boot exposes
  `window.HouseholdTheme`; the settings and people pages are `settings.js` (`SettingsPage`) and `people.js`
  (`PeoplePage`), styled by `settings.css`.
- **Copies carry a header** ("Shared file: edit common/… and run tools/sync_common.py; don't edit this copy.
  sha256=…"); `sync_common.py --check` and `--adopt` as planned.
- **Thin app modules keep the app-facing names** (`auth.get_current_user`, `ha_client.request`, `settings.get`, …),
  so app code and tests didn't change shape; shared modules have no app-specific branches (parameters and hooks).
- **No `config_core`:** after `ha_time` and `auth_core` (options.json → `admin_users`) the `config.py` files only share
  one-line `DATA_DIR` / `DB_PATH` / `SUPERVISOR_*` constants; only Finance reads its version from `config.yaml`.
- **Build files and the docs template are patterns, not copies** (`common/build/`, `common/docs/`), checked by
  `tools/check_build.py`.

## 9. Not shared on purpose (and why)

- **Receipt Price Intelligence: no `db_core`.** It uses SQLAlchemy (`ensure_columns` through the inspector,
  engine-level pragmas); nothing to share.
- **Finance: no `ha_client`, `ha_time` or `housekeeping`.** Its only Home Assistant call is in `bootstrap.py`, which
  reads the time zone and sets `TZ` before uvicorn starts; its background work is its own job worker (`jobs.py`).
- **Finance's App settings page and settings module** stay its own: it stores settings as strings from a server-rendered
  form, with its own messages and no cache, so moving it onto the registry would change its behaviour. Its Users tab (shared access) is not the People page.
- **The admin backup card** (Admin → Storage) isn't shared: each app's Storage page differs in layout, text and
  options; sharing it would change how they look. Only `backup_core` underneath is shared.
- **The HTTP warning in other apps:** only Vault showed one; adding it to Finance, Chat or Family Tree would be a
  visible change, so they are candidates, not done.
- **The `escapeHtml` → `h()` rewrite** of Calorie Tracker's, Splitpot's and Finance's HTML strings was not done: the
  browser check starts with almost no data, so most of those row templates never render and identical output
  couldn't be confirmed; their escaping now uses `UI.escapeHtml`, which escapes all five characters.
- **`ai_usage`** (Finance and Arcade, ~15% alike) and **Receipt's `receipt_llm.py` / `textllm.py`** were not moved onto
  the shared AI client: Finance and Arcade keep usage in different tables with different cost rounding (sharing would
  change the numbers), and Receipt uses Ollama's `/api/chat`, a debug trace of each request and its own fall-back, with
  no Claude provider.
- **Vault's guest Wi-Fi sensor** was not moved onto `sensor_publisher`: it's one entity that checks Home
  Assistant's current state before re-posting a large QR picture, and its delete treats 204 differently.
- As before (§5, §6.4): each app's database and domain logic, Vault's sessions, CSP and crypto, Finance's page
  templates, Arcade's games.

## 10. Candidate fixes found (behaviour changes, not made)

Kept exactly as they behaved, so the refactor changed nothing for users; each is a small fix for a later release.

1. ~~**"Today" on the process time zone**~~ — fixed 2026-10-07: `ha_time` now falls back to the Supervisor's `TZ`
   (never UTC) while Home Assistant isn't answering, keeps asking, and sets the process zone; Vault and Receipt use
   it too, Finance's `bootstrap.py` retries and falls back to the Supervisor's `TZ`; Family Tree's export stamps,
   Splitpot's CSV date, Chat's export and the Assistant's usage days use Home Assistant's zone.
2. **Finance dashboard filters while "viewing as":** the period/account pickers add the acting user as
   `&amp;as_user=…` (the old inline script was HTML-escaped by Jinja), so an admin viewing as someone drops back to
   their own view when changing a filter. `static/pages/dashboard.js` reproduces it on purpose (`data-suffix`); fix:
   `acting_qs | tojson`.
3. **Finance database import:** a rejected upload (integrity / missing tables / not SQLite) leaves its temp `.db` in
   `/data`.
4. **Finance database import confirmation:** the form's `onsubmit="return confirm(…)"` never showed its dialog
   (`confirm` resolved to the form's hidden `<input name="confirm">`), so the required checkbox is the only
   confirmation. Kept (no `data-confirm` on that form; a template comment says why); fix: add `data-confirm`.
5. **Receipt `admin.html`** loads `common/settings.js` and `settings.css` without `?v=` (`main._page` doesn't tag
   them), so a phone may keep an old copy after an update.

## 11. Security fixes 2026-10

The October 2026 security review (low-severity findings only) was fixed in Calorie Tracker 2.1.1, Family Tree 2.2.1,
Finance 1.1.1, Household Arcade 1.6.1, Household Chat 2.2.1, Household Docs 1.0.1, Household Todo 2.3.1, Household
Vault 2.1.1, Receipt Price Intelligence 1.1.1 and Splitpot 2.2.1. New shared pieces: `settings_core.Registry`
`secret_keys()` / `secret_blanks()` / `scrub_secrets()` / `saved_secrets()` / `keep_secrets()`, `backup_core`
`blank_settings()` / `saved_settings()` / `keep_settings()`, `web_security.refuse_cross_site()` /
`cross_origin_websocket()`, the new module `sandbox_run.py` (+ `tests/test_sandbox_run.py`; Finance and Receipt),
and two checks in `tools/check_build.py`.

1. **Secret settings in backup downloads.** The registry knows which settings are secret; `backup_core` blanks them in
   the backup copy and, on restore, puts this install's value back for each one the file leaves blank (a secret an
   older backup does carry is used). Every app with secret App settings uses it: Calorie Tracker and Finance
   (already blanked theirs; now the shared code — Finance keeps the key after migrations instead of before the swap,
   like Calorie Tracker), Arcade (`routers/admin.py`), Household Docs (`routers/admin.py`: docs.db in the zip) and
   Receipt (`services/backup.py`: exports and safety-copy downloads; the safety copies kept on disk stay complete,
   since they are what a failed import puts back). Family Tree, Chat, Todo, Vault and Splitpot have no secret
   settings (Family Tree's map tile URL is sent to browsers, so not a secret; Vault's guest Wi-Fi password is
   by design, see the review). DOCS: "Backups leave out access keys and passwords; after restoring on a new
   install, enter them again."
2. **Uvicorn proxy headers.** Every Dockerfile CMD passes `--no-proxy-headers` (Splitpot's `sh -c` too); Receipt's
   `app/main.py` passes `proxy_headers=False`; Finance's `bootstrap.py` already did. `tools/check_build.py` fails an
   app whose start command doesn't (or turns proxy headers / forwarded-allow-ips on); `common/tests/test_auth_core.py`
   checks that `X-Forwarded-For` / `Forwarded` / `X-Real-IP` naming the proxy never let an outsider in.
3. **PDF tools unprivileged.** `common/python/sandbox_run.py` (`Scratch`): the tool gets a copy of the file in a
   scratch folder owned by `pdfworker` (made in the Finance and Receipt Dockerfiles; checked by `check_build.py`),
   runs as that user with no supplementary groups, a small environment and limits (address space 1 GiB, CPU 120 s,
   files written 512 MB, 64 open files, 64 processes, no core dumps) plus the caller's timeout; as non-root (tests,
   development) only the limits apply; without `pdfworker` it falls back to `nobody`, never root. `lock_down()`
   closes the data folder (0700) at start-up. Finance: `parser/deterministic.py`, `parser/vision.py`, `pdfview.py`,
   `main.py`; Receipt: `services/pdf.py`, `main.py`.
4. **Cross-site requests.** `web_security.refuse_cross_site` in every app's guard middleware, right after the ingress
   check: any method but GET/HEAD/OPTIONS with `Sec-Fetch-Site: cross-site` or `same-site` gets a 403; `same-origin`,
   `none` and no header pass. Finance's `security.py` uses it with its own plain-text 403 (unchanged behaviour).
   Arcade's WebSocket origin check is `web_security.cross_origin_websocket` (same rule). App-bus messages don't go
   over HTTP. Tests: `common/tests/test_web_security.py`, root `tests/test_cross_site.py` (every app, real app).
5. **Receipt DNS rebinding.** `services/websearch.py`: `_check_url` resolves once and returns the checked address;
   the opener's `_PinnedHTTPConnection` / `_PinnedHTTPSConnection` connect to exactly that address (Host header,
   SNI and certificate check keep the name); each redirect is checked and pinned again. A proxy from the
   environment is connected to as before (not used in Home Assistant). Tests: `tests/test_websearch_pinning.py`.
6. **Small ones.** Receipt `/health/model` returns only `configured`, `/health/database` no error text (logged
   instead). Finance `config.yaml` spells out `apparmor: true` and `hassio_api` / `auth_api` / `docker_api` /
   `full_access: false`; `packaging_core.check_config_basics` now requires them for every app.

Not changed: Household Docs runs its own PDF-text and `.xlsx` readers in Python worker processes with their own
limits (`pdftext.py`, `formats/sheet_xlsx.py`), not poppler, so they stay as they are. Receipt's
`logger.info("Imported a backup: %s", counts)` passes a dict as the only argument (a logging error message, no
user-visible effect) — left for a later release.
