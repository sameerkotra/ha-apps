# HA app patterns: multi-theme CSS + admin determination + SQLite export

Three self-contained patterns pulled out of the Finance Dashboard app
so they're easy to drop into another Home Assistant
app — ingress-only, ships its own login-free auth via HA's forwarded
headers, one SQLite file under `/data`.

**Status: implemented in all three apps in this folder** (calorie_tracker,
household_todo, splitpot). Section 4 at the end is the shared maintenance
checklist all three were brought in line with in September 2026 — read it
before starting a new app or changing one of these.

- **calorie_tracker** — already had admin determination (the `switch_admins`
  option / `get_current_user` / `require_admin` / `get_acting_user` chain in
  `app/auth.py` is this pattern, just named differently). Added: the
  multi-theme CSS (`midnight` / `slate` / `daylight`, `app/static/style.css`
  + `index.html` + `app.js`) and the admin-only SQLite backup route
  (`db.backup_to_tempfile()` + `app/routers/admin.py`, gated behind the
  existing `require_admin`). Route name and its own dedicated **Storage**
  sidebar tab (separate from Users) both match the Finance Dashboard
  app's naming: `GET /api/admin-storage-download-db`.
- **splitpot** — had none of the three going in (ingress auth was a
  same-shape but different mechanism: a host-allowlist + header-presence
  `@app.middleware("http")`, not a per-route dependency, and no admin
  concept at all — every request could disable/enable anyone). Added: the
  same multi-theme CSS (`paper` / `slate` / `daylight`, reusing splitpot's
  own `--paper`/`--ink`/`--moss`/... variable names rather than the
  `--bg`/`--panel`/`--accent` names below), a new `admin_users` config
  option + `is_admin()`/`require_admin()` in `main.py` (now also gating the
  People enable/disable toggle, which was previously ungated), and the same
  backup route at `/api/admin/backup-db`.

Below is the original how-to (still accurate as the underlying mechanism —
just read `calorie_tracker`/`splitpot`'s actual files for the two apps'
concrete, slightly-differently-named implementations of it). It applies to
any FastAPI + Jinja2/vanilla-JS + raw-`sqlite3` HA app built the same way.

All three patterns assume the same baseline that Finance Dashboard already has:
a single `style.css` served at `static/style.css`, a `base.html` that every
page `{% extends %}`, and a `db.py` with one `DB_PATH` constant and one
`get_db()` that opens `sqlite3` connections in WAL mode. If your app's
shape differs, adjust file names accordingly — the mechanisms below don't
depend on anything Finance-Dashboard-specific.

---

## 1. Multi-theme CSS (light/dark, user-selectable, no flash)

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
`[data-theme="..."]` attribute selector on `<html>`; a small inline
script in `<head>` reads the saved choice from `localStorage` and sets
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
- **This has to be a plain inline `<script>` in `<head>`, not a bundled/
  deferred script.** The entire point is beating first paint; anything
  that loads after `<body>` starts rendering defeats it.
- **Wrap every `localStorage` call in try/catch.** Private browsing
  modes and some embedded WebViews (HA's own companion apps, for
  instance) can throw on access rather than just returning `null`.
- **`color-scheme: dark` / `color-scheme: light` per theme** is a small
  but real detail — it lets native form controls (date inputs, select
  dropdowns' own OS-level styling) match the theme instead of always
  rendering as if the page were light-mode.

---

## 2. Admin determination (HA ingress headers + a static allowlist)

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

os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8321"])
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


INGRESS_ALLOWED_HOSTS = {"172.30.32.2", "127.0.0.1", "::1"}


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

- **Shared code is copied, not imported — keep copies identical.** Home
  Assistant builds each app from its own folder, so apps can't import
  from each other or from a common folder. Where two apps need the same
  module, the copies must be byte-for-byte identical so a fix is a straight
  copy: today that's `app/ha_notify.py` in Household Todo, Family Tree and
  Household Vault and Household Chat (its docstring says so). Everything else that looks alike (auth,
  settings, admin backup) differs on purpose per app.

- **Time zone data.** `python:3.12-alpine` ships without a zoneinfo
  database, so `ZoneInfo("Europe/Berlin")` raises and the app silently
  runs on UTC ("today" rolls over at UTC midnight). Alpine images need
  `RUN apk add --no-cache tzdata`. (`python:*-slim` / Debian images include
  `tzdata`.) Read HA's zone once at startup from
  `GET http://supervisor/core/api/config`, and use one `today()`/`now()`
  helper everywhere.
- **Pinned dependencies, the same set in every app.** Unpinned
  requirements make every rebuild a different app; old pins collect CVEs.
  Current set: `fastapi==0.141.1`, `uvicorn==0.53.0`
  (`uvicorn[standard]` in splitpot), `python-multipart==0.0.32`, plus
  `requirements-dev.txt` adding `httpx2` for Starlette's `TestClient`.
  Bump them together.
- **`lifespan`, not `@app.on_event("startup")`** (deprecated, and gone in
  Starlette 1.x). Start background loops in the lifespan and cancel them
  in its `finally`.
- **`logging.basicConfig(level=logging.INFO, …)` in `main.py`** — without
  it, INFO logs are dropped and only WARNING+ reach the app log. No
  `print()`.
- **Restore = validate, swap, then migrate.** After `os.replace()` of an
  uploaded backup, call `init_db()` again. A backup from an older version
  may predate a column, and `CREATE TABLE IF NOT EXISTS` + `_migrate()`
  only run at startup otherwise, which means errors until the next restart.
  Create the upload's scratch file in `DATA_DIR` (same filesystem as the
  live DB, or `os.replace` fails with EXDEV).
- **Escape for attributes, not just text.** Frontend `escapeHtml()` must
  escape `&<>"'`. The `div.textContent = s; return div.innerHTML` trick
  does **not** escape quotes, so `title="${escapeHtml(note)}"` is an XSS
  hole. It matters more than usual here: ingress pages are served from
  Home Assistant's own origin, so injected script runs with the viewer's HA
  session. Better still, build DOM nodes with `textContent`
  (household_todo's `h()` helper) and skip HTML strings entirely.
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
  `tests/` folder (`python3 -m unittest discover -s tests`). Use
  `TestClient(app, client=("127.0.0.1", 12345))` so requests pass the
  ingress source check.
- **Settings go in the app, not the app configuration.** `config.yaml`
  holds only the admin list (`admin_users`, or `switch_admins` in
  calorie_tracker) — the bootstrap, since you must be an admin to open App
  settings. Every other setting, including new ones, is an **App setting**:
  an admin-only **🛡️ Admin → App settings** page backed by an
  `app_settings` table (`key`, JSON `value`, `updated_at`, `updated_by`),
  validated with Pydantic (`allow_inf_nan=False`, unknown keys → 422), read
  through a small cache at the moment it's used so changes apply without a
  restart (mark a key `restartRequired` only if that's truly impossible),
  exposed as `GET/PUT /api/admin/settings` → `{values, defaults, meta}`.
  Per-person choices go in that user's own Settings instead. Notify
  targets are assigned per user by admins in Admin → Users (a list of HA's
  `notify` services + manual entry + Send test), never a config option.
  Admin pages (App settings, Users, Storage, …) live under one admin-only
  sidebar item. All four apps follow this since September 2026.
- **Housekeeping:** a `.dockerignore` (`__pycache__`, `*.pyc`, `tests`,
  `*.db*`), a `CHANGELOG.md` per app (HA shows it on update), no
  placeholder `url:` in `config.yaml`, and a version bump plus the
  `?v=` cache-busting query strings in `index.html` on every change.

---

## Minimal checklist to add all three to a new app

1. Copy the `:root` + `[data-theme="..."]` blocks (1a) into the new
   app's stylesheet, using its own existing color values as the
   default/first named theme.
2. Add the theme `<select>` (1b) to its shared nav/header template.
3. Add the bootstrap script (1c) to `<head>`, before the stylesheet's
   effects would otherwise be visible — and the change handler (1d)
   anywhere after the `<select>` renders.
4. Add the `admin_users` list-type option to `config.yaml` (2a), the
   entrypoint script that turns it into an `ADMIN_USERS` env var (2b),
   and `auth.py`'s `get_current_user`/`require_admin` (2c) — plus
   `get_acting_user` if the app scopes data per-user and admins need
   to inspect another user's view.
5. Confirm the app's `db.py` actually opens connections with
   `PRAGMA journal_mode = WAL` — if it doesn't, the plain-file-copy
   caveat in section 3 doesn't apply, but `Connection.backup()` is
   still the safer/simpler choice either way (also correct, and free
   of any need to reason about it).
6. Add the download route (3a) and button (3b) to the app's admin
   page, gated behind `require_admin` (2c).
