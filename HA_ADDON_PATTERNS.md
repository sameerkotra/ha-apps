# HA app patterns: multi-theme CSS + admin determination + SQLite export

Three patterns pulled out of the Finance Dashboard app — page themes, admin determination from HA's
ingress headers, and the admin-only SQLite export — plus the maintenance checklist every household app
follows (section 4). Ingress-only apps, login-free auth via HA's forwarded headers, one SQLite file under
`/data`.

**Status (October 2026): the patterns are now implemented once, in the repository's `common/` folder,**
and copied into every app that uses them by `python tools/sync_common.py` (see `common/README.md` and
`SHARED_CODE_PLAN.md`). An app no longer writes its own version: it uses the shared file and keeps only
what is its own (its accent colours, its routes, its file names and messages). Sections 1–3 below still
explain *why* each piece works the way it does — read them before changing the shared file — and each
starts with where the shared version lives.

| Pattern | Shared files (in `common/`) | Copied to |
|---|---|---|
| 1. Themes (Midnight, Slate, Daylight, Auto; old names mapped) | `static/theme-boot.js`, `static/themes.css` | `app/static/common/` — all 9 apps |
| 2. Ingress source check, identity headers, admin list | `python/auth_core.py` (`refuse_outsiders`, `identity`, `admin_names` / `admin_entries`, `is_admin`, `no_admin`, `require_admin_flag`) | `app/common/` — all 9 |
| 2. "How the app sees you" + "No admin yet" | `python/whoami.py`, `static/whoami.js` (`WHOAMI_PAGE_SPEC.md`) | all 9 (`whoami.js`: all but Finance) |
| 3. Database export / import | `python/db_core.py` (`connect`, `transaction`, `add_missing_columns`, `snapshot_to_tempfile`, `validate_file`, `swap_in`), `python/backup_core.py` (`file_name`, `send_file`, `write_zip`, `receive`, `check_members`, `restore_file`, …) | `db_core`: all but Receipt (SQLAlchemy); `backup_core`: all 9 |
| 4. App settings page | `python/settings_core.py`, `static/settings.js`, `static/settings.css` | all but Finance |
| 4. Admin → People / Users (phones from HA, extra notify services, Send a test) | `python/people_admin.py`, `static/people.js` | Family Tree, Arcade, Chat, Todo, Vault (`people.js` also Calorie Tracker, Splitpot) |
| 4. Logging, lifespan jobs | `python/housekeeping.py` (`setup_logging`, `Jobs`, `periodic`) | all but Finance and Receipt |
| 4. Security headers (CSP, `nosniff`, Cache-Control) | `python/web_security.py` | all 9 |
| 4. Escaping and DOM building | `static/ui.js` (`UI.h`, `UI.escapeHtml`, `UI.makeApi`, `UI.toast`, …) | all 9 |
| 4. Build files | `build/Dockerfile.template`, `build/requirements-base.txt`, checked by `python tools/check_build.py` | not copied (patterns) |
| 4. Test helpers and packaging checks | `tests/env.py`, `tests/ingress.py`, `tests/packaging_core.py`, `tests/fake_ha.py` | `tests/common_tests/` |

`common/manifest.json` says exactly which app gets which file. Never edit a copy in an app (its first line
says "Shared file: edit common/…"): edit `common/`, run `python tools/sync_common.py`, then the tests.

The original how-to follows. It applies to any FastAPI + Jinja2/vanilla-JS + raw-`sqlite3` HA app built
the same way; the code samples show the mechanism, the shared files are the version to use.

---

## 1. Multi-theme CSS (light/dark, user-selectable, no flash)

**Shared now:** `common/static/themes.css` (the surfaces, text and state colours of Midnight — the default
— Slate and Daylight: `--bg --panel --panel-alt --panel-hover --card --border --text --text-dim`,
`--danger(-soft) --warn(-soft) --ok(-soft) --info(-soft)` and a neutral accent) and
`common/static/theme-boot.js` (`window.HouseholdTheme`: applies the saved `theme` and the collapsed
sidebar before first paint; `bindSelect`, `set`, `choice`, `onChange`, `sidebarCollapsed`,
`setSidebarCollapsed`). **Auto** follows the device (Daylight when it is light, else Midnight); old saved
names keep working (heritage, ink, vault, paper → Midnight; parchment, sandstone → Daylight). Each app's
`style.css` sets only its accent and its own colours, in the three blocks `:root, [data-theme="midnight"]`,
`[data-theme="slate"]`, `[data-theme="daylight"]`. In `<head>`, before the app's stylesheet:
`<script src="common/theme-boot.js?v=…"></script>` then `<link rel="stylesheet" href="common/themes.css?v=…">`
(Finance and Receipt: `static/common/…`). It is an **external** script, not an inline one: every app's
Content-Security-Policy forbids inline scripts (section 4).

### What this gets you
- Several complete color palettes (dark and light), switchable instantly
  at runtime with no page reload.
- The user's choice persists across page loads and across every page in
  the app (a real multi-page app, not an SPA).
- No flash of the wrong theme on navigation — the correct palette is
  applied before the page paints, not after.
- Zero theme-specific overrides scattered through the rest of the
  stylesheet — every rule just uses a handful of CSS custom properties,
  and picking a theme is nothing but which block currently supplies their
  values.

### The mechanism, in one sentence
Every theme is a same-shaped set of CSS custom properties under a
`[data-theme="..."]` attribute selector on `<html>`; a small script
in `<head>` (now the external `common/theme-boot.js`) reads the saved choice from `localStorage` and sets
that attribute *before* anything below it paints; a `<select>` later in
the page lets the user change it instantly (pure CSS variable swap, no
JS re-render needed) and writes the new choice back to `localStorage`.

### 1a. Define the variable set once, then repeat it per theme (`style.css`)

```css
:root {
    /* Non-palette vars — shared across every theme, not redeclared per
       theme. Add your own layout constants here too. */
    --radius: 12px;
}

/* The default theme is also the :root palette — one block, two selectors —
   so what a client sees before the bootstrap script (1c) runs, or with
   JS/localStorage unavailable, can never drift from the named theme. */
:root, [data-theme="midnight"] {
    --bg: #10151c;
    --panel: #1a2130;
    --panel-alt: #202a3d;
    --panel-hover: #26314a;
    --border: #2b3549;
    --text: #e7ecf5;
    --text-dim: #93a0b8;
    --accent: #5ee6a8;
    --accent-dim: #3a9c72;
    --accent-contrast: #0b1410;
    --danger: #ef7b7b;
    --danger-bg: #3a2229;
    --warn: #f2c94c;
    --warn-bg: #332c1a;
    --ok: #5ee6a8;
    --ok-bg: #1a2e26;
    --info: #6aa6ff;
    --info-bg: #1a2540;
    --code-bg: #0b0f15;
    --code-text: #b6c2d9;
    color-scheme: dark; /* lets native form controls (date pickers, etc.)
                            theme themselves too — "light" for a light
                            theme block */
}

[data-theme="slate"] {
    --bg: #0e1420;
    --panel: #16202f;
    --accent: #6aa6ff;
    /* ...same full variable set, different hue family... */
    color-scheme: dark;
}

[data-theme="daylight"] {
    --bg: #f4f6fa;
    --panel: #ffffff;
    --text: #1c2430;
    --accent: #2f6fed;
    /* ...same full variable set, light values... */
    color-scheme: light;
}

/* Add as many more as you want — same shape every time. */
```

Every other rule in the stylesheet just uses `var(--bg)`, `var(--accent)`,
etc. — never a hardcoded color, never a `[data-theme="x"] .some-class {}`
override anywhere else in the file. That's what makes adding a 5th theme
later a pure copy-paste of one block with new hex values, touching nothing
else.

### 1b. The theme `<select>` (inside your sidenav/header, `base.html`)

```html
<select id="theme-select" class="theme-select" title="Theme" aria-label="Theme">
    <option value="midnight">🌙 Midnight</option>
    <option value="slate">🌆 Slate</option>
    <option value="daylight">☀️ Daylight</option>
    <option value="sandstone">🏜️ Sandstone</option>
</select>
```

### 1c. The pre-paint bootstrap script (`<head>`, before `<link rel="stylesheet">`'s
effects would otherwise flash the default)

```html
<script>
(function () {
    var THEMES = ["midnight", "slate", "daylight", "sandstone"]; // keep in sync with 1a/1b
    try {
        var theme = localStorage.getItem("theme");
        // Validated against the known theme list, not trusted blindly —
        // a stale/invalid stored value (an old theme name that no
        // longer exists, e.g. after you remove one) falls back to the
        // default instead of setting a [data-theme] attribute nothing
        // in the stylesheet matches, which would render unstyled.
        if (theme && THEMES.indexOf(theme) !== -1) {
            document.documentElement.setAttribute("data-theme", theme);
        }
    } catch (e) { /* localStorage unavailable (private mode, etc.) — stays on the :root default */ }
})();
</script>
```

This has to run synchronously in `<head>`, before any content renders —
not deferred, not at the bottom of `<body>`. Since this is a real
multi-page HA app (every navigation is a full page load, not a
client-side route change), this script re-runs on every single page —
that's the point, and it's cheap enough that it doesn't matter.

### 1d. The change handler (anywhere after the `<select>` renders)

```html
<script>
(function () {
    var select = document.getElementById("theme-select");
    if (!select) return;
    // Reflects whatever the bootstrap script (1c) already applied (or
    // the :root default it left in place) — the <select>'s own markup
    // can't express a dynamic "selected" state server-side, since the
    // choice lives in localStorage, not anything the template has
    // access to.
    var current = document.documentElement.getAttribute("data-theme") || "midnight";
    select.value = current;
    select.addEventListener("change", function () {
        var theme = select.value;
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("theme", theme); } catch (e) { /* ignore */ }
    });
})();
</script>
```

### Gotchas / things that bit us building this the first time
- **Give the default theme's block both selectors: `:root, [data-theme="…"]`.**
  `:root` is the palette shown before JS/localStorage has a chance to run
  at all; writing it out twice invites drift (a flash between two slightly
  different "same" themes on first load). One block can't drift.
- **Validate the stored value against a known list before applying it.**
  Renaming or removing a theme later leaves old `localStorage` values
  around in returning users' browsers; an unvalidated stale name sets a
  `[data-theme]` attribute nothing matches, which renders completely
  unstyled (worse than just falling back to default).
- **This has to be a synchronous `<script>` in `<head>`, not a bundled/
  deferred one.** The entire point is beating first paint; anything
  that loads after `<body>` starts rendering defeats it. An external file
  (`common/theme-boot.js`, no `defer`/`async`) works just as well as an
  inline block and keeps the CSP strict (`script-src 'self'`).
- **Wrap every `localStorage` call in try/catch.** Private browsing
  modes and some embedded WebViews (HA's own companion apps, for
  instance) can throw on access rather than just returning `null`.
- **`color-scheme: dark` / `color-scheme: light` per theme** is a small
  but real detail — it lets native form controls (date inputs, select
  dropdowns' own OS-level styling) match the theme instead of always
  rendering as if the page were light-mode.

---

## 2. Admin determination (HA ingress headers + a static allowlist)

**Shared now:** `common/python/auth_core.py` — the ingress source check (`INGRESS_PROXY` 172.30.32.2,
`INGRESS_HOSTS` with loopback, `client_host`, `from_ingress`, `refuse_outsiders`), the identity headers
(`identity()` → `Identity(user_id, username, display_name, ingress_path)`), the 401 text
(`no_user_message`, `require_user_id`), loading `admin_users` (`read_options`, `admin_entries`,
`admin_names`, with `strip_quotes` and `fold`; `env_admins` for `DEV_ADMINS` / `DEV_ADMIN_USERS`) and
matching (`is_admin` by id or login name, `display_name_listed`, `display_name_only`, `no_admin`,
`require_admin_flag`). Each app's `auth.py` (Splitpot: `main.py`; Finance: `auth.py` + `security.py`)
is a thin layer over it with its own names, messages and extra rules (Vault sessions, Todo "acting as",
Finance "view as"). "How the app sees you" and the "No admin yet" banner are `common/python/whoami.py`
and `common/static/whoami.js` (`WHOAMI_PAGE_SPEC.md`).

### What this gets you
- No app-local login/password system at all — identity comes
  entirely from Home Assistant's own ingress auth, which every HA user
  already has, so there's nothing new to sign into.
- A simple, static "who's an admin in this app" allowlist that lives
  in the app's own config (an HA-native list-type option), not a
  live call out to HA's user directory.
- Fails closed: without genuine proof the request actually came
  through Supervisor's ingress proxy, nobody is trusted — admin or
  not.

### The mechanism, in one sentence
Supervisor's ingress proxy forwards `X-Remote-User-Id` /
`X-Remote-User-Name` / `X-Remote-User-Display-Name` on every request to
an ingress-only app. **Headers alone prove nothing** — any other
container on the internal Docker network can reach this one and send
whatever headers it likes, `X-Ingress-Path` included (an earlier version
of this doc wrongly said only Supervisor could set that one). What makes
the headers trustworthy is the **TCP source address**: a middleware
rejects every request that doesn't come from Supervisor's ingress proxy
(`172.30.32.2`) or loopback, before any route runs. After that, a request
is "this user" per `X-Remote-User-Id`, and "an admin" only if their **user
id or login name** is in a static `ADMIN_USERS` allowlist, sourced from
the app's own config, not from HA's user directory. Display names are
never matched (see Gotchas).

### Why a static list instead of asking HA who's an admin
HA does have a live API for this (`config/auth/list` over the
Supervisor websocket), but it needs extra Supervisor API access scope
granted to the app, is one more network call that can fail or time
out, and is solving a problem a static list already solves for the
actual use case here: a household-sized, rarely-changing set of
people. If your app's admin set is large or changes often, the live
API becomes worth the extra complexity — for most HA apps built for
a household, it isn't.

### 2a. config.yaml: a real list-type option

```yaml
# admin_users is a genuine HA list-type option (an "Add" button + one
# input per entry in the config UI), not a single comma-separated text
# field — nicer UI for something that's inherently a list of names.
options:
  admin_users: []
schema:
  admin_users:
    - str
```

### 2b. bootstrap.py (or your app's own entrypoint): config → env var

HA writes app config to `/data/options.json` at container start.
Since the app reads its admin list at *module-import* time (below), it
has to already be in the environment before the app process starts —
hence a tiny entrypoint script that reads the JSON, sets the env var,
then execs into the real server process:

```python
import json
import os

OPTIONS_PATH = "/data/options.json"

if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH) as f:
        options = json.load(f)
    # admin_users arrives as a JSON array (the list-type option above)
    # — join it into the comma-separated string the app actually
    # parses, rather than changing the app's parsing just because the
    # config UI's shape changed.
    os.environ["ADMIN_USERS"] = ",".join(options.get("admin_users", []))
else:
    # Not running as an HA app (e.g. local dev) — the app's own env
    # vars, if set directly, still work; this just means there's no
    # options.json to layer on top of them.
    pass

# --no-proxy-headers: the source-address check needs the real TCP peer, never X-Forwarded-For.
os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8321", "--no-proxy-headers"])
```

### 2c. auth.py: the actual identity + admin-check dependency chain

```python
import os
from dataclasses import dataclass
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse

ADMIN_USERS = {
    u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()
}


@dataclass
class User:
    id: str
    name: str
    is_admin: bool


INGRESS_ALLOWED_HOSTS = {"172.30.32.2", "127.0.0.1", "::1"}   # shared: auth_core.INGRESS_HOSTS


@app.middleware("http")
async def require_ha_ingress(request: Request, call_next):
    # The ONLY thing that makes the X-Remote-User-* headers trustworthy:
    # the request really came from Supervisor's ingress proxy.
    if (request.client.host if request.client else None) not in INGRESS_ALLOWED_HOSTS:
        return JSONResponse(status_code=403, content={"detail": "Access only via Home Assistant"})
    return await call_next(request)


def get_current_user(request: Request) -> User:
    user_id = request.headers.get("x-remote-user-id")
    if not user_id:
        # Supervisor didn't identify anyone (e.g. an auth provider that
        # doesn't populate this). Fail closed: no identity, no access.
        raise HTTPException(status_code=401, detail="Not authenticated via Home Assistant ingress")

    login = request.headers.get("x-remote-user-name")   # may be absent (some auth providers)
    name = request.headers.get("x-remote-user-display-name") or login or user_id
    # Matched against the user id and the LOGIN name only (case-insensitive,
    # ADMIN_USERS stored lower-cased) — never the display name, which isn't
    # unique or stable. A "How the app sees you" page showing the id and
    # login name is what tells the HA admin exactly what to paste.
    candidates = {c.strip().lower() for c in (user_id, login) if c}
    return User(id=user_id, name=name, is_admin=bool(candidates & ADMIN_USERS))


def require_admin(current: User = Depends(get_current_user)) -> User:
    """Use as a route dependency on anything actually admin-gated
    (403s a non-admin) — as opposed to just reading current.is_admin to
    decide what to SHOW a user (e.g. an admin-only nav link), which
    doesn't block the request on its own."""
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return current
```

### Bonus: an admin "view as" pattern, if your app scopes data per-user

If your app (like Finance Dashboard) scopes each user's data to
themselves but wants admins able to inspect/help debug another user's
view, layer one more dependency on top rather than threading an
`as_user` param through every route by hand:

```python
def get_acting_user(
    request: Request,
    current: User = Depends(get_current_user),
) -> User:
    """The user whose DATA this request should operate on. For a
    regular user this is always themselves — an as_user param is
    silently ignored, not honored and not even inspected further, so
    there's no path where a non-admin's own request parameters alone
    can widen their own access. For an admin, ?as_user=<id> switches
    scope to that user."""
    if not current.is_admin:
        return current

    target_id = request.query_params.get("as_user")
    if not target_id or target_id == current.id:
        return current

    return User(id=target_id, name=target_id, is_admin=False)
```

Every data-scoped route then depends on `get_acting_user` (never
`get_current_user` directly) for "whose rows do I query", while still
having `current` available separately for "who's actually making this
request" (audit trails, "acting as" banners, etc).

### Gotchas
- **Check the source address, not a header.** Every app here has an
  `@app.middleware("http")` that 403s any request whose
  `request.client.host` isn't `172.30.32.2` (Supervisor's ingress proxy),
  `127.0.0.1` or `::1`. Without it, another app on the same Docker
  network could send `X-Remote-User-Id: <an admin's id>` straight to the
  container. Then fail closed on a missing `X-Remote-User-Id` (401).
- **Start uvicorn with proxy headers off** (`--no-proxy-headers`, or
  `proxy_headers=False` from Python): otherwise `request.client.host` can
  come from `X-Forwarded-For` (uvicorn trusts it from `--forwarded-allow-ips`,
  default 127.0.0.1), which a setting or a changed default would open up.
  `tools/check_build.py` checks every app's start command.
- **Refuse cross-site writes**: right after the source check, the shared
  `web_security.refuse_cross_site` 403s any request but GET/HEAD/OPTIONS that
  the browser labels `Sec-Fetch-Site: cross-site` or `same-site` (no header,
  `same-origin` and `none` pass). A WebSocket route checks `Origin` itself
  (`web_security.cross_origin_websocket`).
- **Match `id` and login name only — never the display name.** Display
  names aren't unique and aren't an identity: two people can share one,
  and matching them lets someone become an admin just by having (or
  getting) an admin's name. All three apps log a one-time warning when
  an allowlist entry matches someone *only* by display name, naming the
  id to use instead, and their "How the app sees you" page says the same.
- **Don't ship this as a single comma-separated text field** in
  `config.yaml` if HA's list-type option (`schema: admin_users: - str`)
  is available to you — it's a strictly nicer config UI (an "Add"
  button + one input per entry) for something that's inherently a list.
- **Read `ADMIN_USERS` at module-import time, and make sure it's in the
  environment before that import happens** — that's the whole reason
  the entrypoint script (2b) execs into the real server rather than the
  app reading `/data/options.json` itself at request time.
- **`require_admin` (blocks the request) and `current.is_admin` (just a
  bool to branch UI on) are different tools** — a template that hides
  an admin-only link based on `is_admin` is a UI nicety, not a security
  boundary; the route behind that link still needs its own
  `require_admin` dependency, since nothing stops a user from just
  typing the URL directly.

---

## 3. Admin-only SQLite database export

**Shared now:** `common/python/db_core.py` (`connect` with the app's pragmas, `transaction`, `closing`,
`columns`, `add_missing_columns`, `snapshot` / `snapshot_to_tempfile`, `validate_file`, `swap_in`) and
`common/python/backup_core.py` (`file_name` — the stamped name, `send_file` — a temp file sent then
deleted, `walk` + `write_zip` — a zip from the snapshot and the app's extra files, `receive` /
`receive_sync` — an upload streamed into a temp file next to the database with a size limit, `open_zip`,
`check_members` — safe member names plus the app's own check, `copy_out`, `restore_file` — `swap_in` and
the app's migrations under its lock). Each app keeps its routes, file names, zip layout, messages, status
codes and post-restore steps. The sample below is what `snapshot_to_tempfile` + `send_file` do.

### What this gets you
- A one-click "download the whole database" button, gated to admin
  users only, for a manual backup or to poke around with an external
  SQLite tool (DB Browser for SQLite, etc.).
- A **correct, consistent** snapshot even while the app is actively
  running and taking writes — not a torn/partial copy.
- Nothing left behind on the server's disk afterward, success or
  failure.

### Why you can't just serve the `.db` file directly
If your `db.py` opens connections in WAL mode (recommended for any app
with background workers or htmx polling, since it lets reads proceed
without blocking on a writer), recently-committed data can still be
sitting in the accompanying `-wal` file rather than the main `.db` file
on disk. A plain `FileResponse(DB_PATH)` reads only the main file — it
can silently hand back a **stale or incomplete** snapshot, missing
whatever hasn't been checkpointed yet. This is easy to miss in testing
(a quiet dev DB checkpoints promptly) and easy to hit in production (an
app with real concurrent activity).

The fix is to use SQLite's own **online backup API**
(`sqlite3.Connection.backup()`), which is built for exactly this: it
produces one complete, internally consistent file regardless of WAL
state, safely even against a database still taking writes.

### 3a. The route (adapt to your own auth dependency and route prefix)

```python
import os
import sqlite3
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .db import DB_PATH          # wherever your app defines this
from .auth import User, require_admin   # your own admin-gating dependency

router = APIRouter()


@router.get("/admin-storage-download-db")
def admin_storage_download_db(admin: User = Depends(require_admin)):
    """Admin-only: downloads a full, consistent snapshot of the app's
    entire SQLite database — for a manual backup or to open with an
    external SQLite tool.

    Deliberately NOT a plain FileResponse(DB_PATH): if the connection
    pool runs in WAL mode, recently-committed data can still be sitting
    in the accompanying -wal file rather than the main .db file on
    disk — reading DB_PATH directly could silently hand back a stale/
    incomplete snapshot. sqlite3's own online backup API
    (Connection.backup()) is the correct tool for exactly this: it
    produces one consistent, complete file regardless of WAL state,
    safely even against a live database still taking writes.

    The backup is written to a fresh temp file (never inside any
    directory the app itself scans/tracks — you don't want your own
    "orphaned files" admin view flagging your own backup as garbage)
    and is deleted right after the response finishes sending, so
    nothing lingers on disk regardless of whether the download
    actually completes on the client end."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(DB_PATH)
    try:
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    filename = f"{ADDON_NAME}-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return FileResponse(
        tmp_path,
        media_type="application/vnd.sqlite3",
        filename=filename,
        background=BackgroundTask(os.remove, tmp_path),
    )
```

Replace `ADDON_NAME` with a short slug for whichever app this is
(`finance`, `calorie-tracker`, ...) so the downloaded filename is
self-identifying once it's sitting in a Downloads folder next to other
apps' backups.

### 3b. The button (wherever your admin page lives)

```html
<a href="admin-storage-download-db" class="btn-secondary">Download database (.db)</a>
```

If your button classes (`.btn-primary`/`.btn-secondary` or equivalent)
were only ever used on `<button>` elements before, make sure they also
set `display: inline-block; text-decoration: none;` — otherwise an
anchor styled with the same class renders with a browser-default
underline and inline (not button-shaped) layout.

### Gotchas
- **Always use `Connection.backup()`, never a raw file copy, if your
  DB runs WAL mode.** This is the whole reason this pattern exists —
  skip it and you'll occasionally ship an admin a backup that's missing
  the last few minutes of writes, in a way that's very hard to notice
  until it's actually needed.
- **Close both connections** (`src` and `dst`) even if `backup()`
  raises — that's what the nested `try/finally` is for. A connection
  leak here is easy to introduce by "simplifying" this into a flat
  sequence of calls.
- **Delete the temp file via `BackgroundTask`, not before/after
  `return`.** `FileResponse` streams the file lazily as part of sending
  the response; deleting it before `return` serves nothing, and
  deleting it synchronously after `return` (impossible anyway, since
  `return` ends the function) would race the still-in-progress stream.
  `BackgroundTask` runs after the response has actually finished
  sending.
- **Put the temp file somewhere your own app doesn't scan.** If your
  app has its own "here's every file sitting in our pending/upload
  directory" admin view (a common pattern for cleanup/debugging), make
  sure this backup's temp file lives outside that directory — otherwise
  you'll see your own backups flagged as orphaned files by your own
  tooling, in the (usually sub-second) window before the
  `BackgroundTask` cleans them up.
- **For a genuinely huge database** (multi-GB — not the common case for
  a household HA app), `backup()` accepts `pages=` and `sleep=`
  arguments to checkpoint incrementally instead of copying everything
  in one call; the default (copy everything in one shot) is fine for
  anything in the tens-to-low-hundreds-of-MB range typical of this kind
  of app.
- **Gate this behind the same admin check as the rest of your admin
  pages** — this route hands back *every* user's data in one file, not
  just the acting user's, so it needs to be at least as strict as
  whatever your most sensitive existing admin route already requires.

---

## 4. Maintenance checklist (all three apps follow this)

Things each app got wrong at least once — check them in any new one.

- **Shared code is copied, not imported — never edit a copy.** Home
  Assistant builds each app from its own folder, so apps can't import
  from each other or from a common folder at run time. Shared code lives
  once in `common/` and `python tools/sync_common.py` copies it into each
  app listed in `common/manifest.json` (Python → `app/common/`, browser
  files → `app/static/common/`, test helpers → `tests/common_tests/`), each
  copy with a "Shared file: edit common/…" header and its hash.
  `sync_common.py --check` (run by the repository tests) and each app's
  `tests/common_tests/test_shared_copies.py` fail when a copy drifts. What
  differs per app stays in the app's own thin module built on the shared one.

- **Time zone data.** `python:3.12-alpine` ships without a zoneinfo
  database, so `ZoneInfo("Europe/Berlin")` raises and the app silently
  runs on UTC ("today" rolls over at UTC midnight). Alpine images need
  `RUN apk add --no-cache tzdata`. (`python:*-slim` / Debian images include
  `tzdata`.) Read HA's zone at startup from
  `GET http://supervisor/core/api/config`, and use one `today()`/`now()`
  helper everywhere. Home Assistant often isn't answering yet when an app
  starts (after a reboot the apps start first): never fall back to UTC —
  use the `TZ` the Supervisor puts in every app's container (HA's zone),
  keep asking until HA answers, and set the process's `TZ` (`time.tzset()`)
  so a stray `date.today()` agrees. `common/python/ha_time.py` does all of
  this (`ha_time.load` / `start_blocking`).
- **Pinned dependencies, the same set in every app.** Unpinned
  requirements make every rebuild a different app; old pins collect CVEs.
  The shared pins are `common/build/requirements-base.txt` (fastapi,
  uvicorn — `uvicorn[standard]` in splitpot —, python-multipart in every
  app, the others where used, `httpx2` for Starlette's `TestClient` in
  `requirements-dev.txt`); each app keeps its own `requirements.txt` with
  exactly those versions. The Dockerfile follows
  `common/build/Dockerfile.template` (pinned Python base, `tzdata`,
  requirements first, `COPY app ./app` so the shared copies reach the image).
  `python tools/check_build.py` (also run by the repository tests) checks
  every app's requirements, Dockerfile and `.dockerignore`. Bump them together.
- **`lifespan`, not `@app.on_event("startup")`** (deprecated, and gone in
  Starlette 1.x). Start background loops in the lifespan and cancel them
  in its `finally` — with the shared runner, `common/python/housekeeping.py`
  (`Jobs().every()` / `add()`, `start()`, `await stop()`; `periodic()` for a
  loop function).
- **Set up logging at INFO in `main.py`** — `housekeeping.setup_logging()`
  (a `logging.basicConfig(level=logging.INFO, …)` with the shared format);
  without it, INFO logs are dropped and only WARNING+ reach the app log. No
  `print()`.
- **Restore = validate, swap, then migrate.** After `os.replace()` of an
  uploaded backup, call `init_db()` again. A backup from an older version
  may predate a column, and `CREATE TABLE IF NOT EXISTS` + the migrations
  only run at startup otherwise, which means errors until the next restart.
  Create the upload's scratch file in `DATA_DIR` (same filesystem as the
  live DB, or `os.replace` fails with EXDEV). The shared pieces do exactly
  this: `backup_core.receive` (scratch file next to the database),
  `db_core.validate_file`, `backup_core.restore_file` (`db_core.swap_in`,
  then the app's migrations under its lock); new columns go in the app's
  `MIGRATIONS` list (`db_core.add_missing_columns`).
- **Escape for attributes, not just text.** Frontend `escapeHtml()` must
  escape `&<>"'`. The `div.textContent = s; return div.innerHTML` trick
  does **not** escape quotes, so `title="${escapeHtml(note)}"` is an XSS
  hole. It matters more than usual here: ingress pages are served from
  Home Assistant's own origin, so injected script runs with the viewer's HA
  session. `common/static/ui.js`'s `UI.escapeHtml` escapes all five.
  Better still, build DOM nodes with `textContent` (`UI.h()`) and skip
  HTML strings entirely.
- **A Content-Security-Policy everywhere.** Every app sends one through
  `common/python/web_security.py` (`SecurityHeaders(CSP, …)`, its `CSP`
  string in `main.py`, plus `X-Content-Type-Options: nosniff` and its
  Cache-Control rules): `script-src 'self'`, so no inline `<script>`
  blocks, no `on…=` attributes, no `javascript:` URLs; values a page script
  needs from the server go in `data-*` attributes.
- **Validate numbers as finite and in range.** JSON allows `Infinity` and
  `NaN`, and Pydantic accepts them by default. Use
  `Field(gt=0, le=…, allow_inf_nan=False)` on money and quantities, and
  check that every referenced id (group members, split people) actually
  belongs to the thing it's attached to. Duplicates in a list that feeds a
  composite primary key are a 500, not a 400, unless you check first.
- **No network calls while holding a write lock or an open DB
  connection.** Fetch first (with a cache if it's heavy, like
  `GET /api/states`), then take the lock and write.
- **Tests exist and run with the pinned deps.** Each app has a
  `tests/` folder (`python3 -m unittest discover -s tests`; Finance:
  `pytest`). Use `TestClient(app, client=("127.0.0.1", 12345))` so requests
  pass the ingress source check (`common_tests/ingress.py`:
  `ingress_client`, `identity_headers`, `user_headers`). The shared
  packaging checks are `common_tests/packaging_core.py`; each app's
  `test_packaging.py` runs them with its own values.
- **Settings go in the app, not the app configuration.** `config.yaml`
  holds only the admin list (`admin_users`; Finance also `trusted_client_ips`)
  — the bootstrap, since you must be an admin to open App
  settings. Every other setting, including new ones, is an **App setting**:
  an admin-only **🛡️ Admin → App settings** page backed by an
  `app_settings` table (`key`, JSON `value`, `updated_at`, `updated_by`),
  validated with Pydantic (`allow_inf_nan=False`, unknown keys → 422), read
  through a small cache at the moment it's used so changes apply without a
  restart (mark a key `restartRequired` only if that's truly impossible),
  exposed as `GET/PUT /api/admin/settings` → `{values, defaults, meta, groups}`.
  Shared now: the app lists its settings once (`Setting(...)`, `Group(...)`)
  on `common/python/settings_core.py`'s `Registry` (validation, storage,
  cache, the payload), and `common/static/settings.js` draws the page from
  `meta` (one card per group, help, range and default, Save / Discard
  changes), so a new setting needs no page code. Per-person choices go in
  that user's own Settings instead. Notify targets are assigned per user by
  admins in Admin → Users (phones from Home Assistant, a list of HA's
  `notify` services + manual entry + **Send a test**:
  `common/python/people_admin.py` + `common/static/people.js`), never a
  config option. Admin pages (App settings, Users, Storage, …) live under
  one admin-only sidebar item. Every app follows this (Finance keeps its
  own settings page).
- **Housekeeping:** a `.dockerignore` (`__pycache__`, `*.pyc`, `tests`,
  `*.db*`), a `CHANGELOG.md` per app (HA shows it on update), no
  placeholder `url:` in `config.yaml`, and a version bump plus the
  `?v=` cache-busting query strings in `index.html` on every change.

---

## Minimal checklist to add all three to a new app

1. Add the app to `common/manifest.json` with the shared files it uses
   (at least `__init__.py`, `auth_core.py`, `whoami.py`, `db_core.py`,
   `backup_core.py`, `web_security.py`; `theme-boot.js`, `themes.css`,
   `ui.js`, `whoami.js`, `backnav.js`; the test helpers), then run
   `python tools/sync_common.py`.
2. Themes (section 1): load `common/theme-boot.js` and `common/themes.css`
   in `<head>` before the app's stylesheet, set the app's accent in the
   three theme blocks of its `style.css`, and fill the theme `<select>`
   with `HouseholdTheme.bindSelect(select)`.
3. Admins (section 2): the `admin_users` list-type option in `config.yaml`
   (2a), a guard middleware calling `auth_core.refuse_outsiders`, and an
   `auth.py` with `get_current_user` / `require_admin` built on `auth_core`
   — plus `get_acting_user` if the app scopes data per-user and admins need
   to inspect another user's view. Add the `/api/whoami` route with
   `whoami.build()` and the "No admin yet" banner with `whoami.js`.
4. Database (section 3): `db.py` on `db_core.connect` (WAL) and
   `db_core.transaction`, a `MIGRATIONS` list, and the download / import
   routes on `backup_core`, gated behind `require_admin`.
5. Security headers: a `CSP` string in `main.py` passed to
   `web_security.SecurityHeaders`, no inline scripts.
6. Build files: follow `common/build/Dockerfile.template`, pin as in
   `common/build/requirements-base.txt`, and run `python tools/check_build.py`.
