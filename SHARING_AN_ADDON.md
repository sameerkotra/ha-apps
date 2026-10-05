# Making a Home Assistant app usable by other people

A playbook from turning the Finance Dashboard (a personal, one-household app) into
something anyone can install. It pairs with `HA_ADDON_PATTERNS.md` (themes, admin
determination, SQLite export, "How the app sees you"), which covers the building blocks
this one assumes.

The short version: **move everything personal out of the app configuration and into
the app, make region- or vendor-specific parts optional, let people bring their own AI,
tell them where their data goes, give a fresh install a way to start, package it as an
app repository, and publish a clean copy without your history.**

---

## 1. Settings live in the app, not in the app configuration

**Why.** App options need a restart to change, can only be edited by HA admins in the
Supervisor UI, and invite you to ship your own values as defaults (the Finance app
shipped its owner's own LAN address as the default `ollama_url`). An in-app settings page changes things
immediately and has room for help text, validation and a "test" button.

**What stays an app option:** only what the app needs before anyone can log in —
the admin list (`admin_users`) and anything about the network path in (`trusted_client_ips`).
Both default to empty.

**How it's built (Finance: `app/settings.py`, `routes/settings_page.py`, migration 0022):**

- A key/value table: `app_settings (key PRIMARY KEY, value, updated_at, updated_by)`.
- One registry of settings, each with key, label, kind, default, help:
  kinds `url | text | bool | price | int | choice | secret`. A `GROUPS` list says which
  section of the page each belongs to. Adding an option = one `Setting(...)` line + its key in
  a group; the page renders it.
- `get(key)` reads the table on **every use** (it's SQLite, it's cheap). No caching means
  no restart and no stale value.
- `save(values)` validates everything first and saves nothing if any value is wrong; the
  form keeps what was typed and lists the errors. A bool missing from the form means
  "unticked".
- The page is admin-only (`require_admin`), app-wide (not per acting user), and shows
  "Last changed … by …" under each field.
- **Upgrading an install that used app options:** leave `bootstrap.py` passing any
  leftover option to an env var, and `seed_from_addon_options()` at startup copies it into the
  table **only if the table has no value** (`INSERT OR IGNORE`). After that the page owns it.
- Put the page under **Admin** (Finance: Admin → App settings), not in a user section.
- Hide the settings table from any query/report feature.

## 2. Bring-your-own AI (if the app uses a model)

**One client for every AI use** (Finance: `app/ai_client.py`). Nothing else in the code
builds HTTP requests to a model; everything calls
`generate(prompt, images=…, want_json=…, timeout=…, purpose=…)` and gets back
text, raw body and token counts.

Providers worth supporting, and what differs:

| | Ollama | OpenAI-compatible | Anthropic Claude |
|---|---|---|---|
| Endpoint | `POST {url}/api/generate` | `POST {url}/chat/completions` | `POST {url}/v1/messages` |
| Default address | none (must be typed) | `https://api.openai.com/v1` | `https://api.anthropic.com` |
| Auth header | — | `Authorization: Bearer <key>` (only if a key is set: LM Studio etc. need none) | `x-api-key`, `anthropic-version: 2023-06-01` |
| Images | `images: [base64]` | content parts `image_url` with `data:image/png;base64,…` | content blocks `{"type":"image","source":{"type":"base64",…}}` before the text |
| JSON mode | `format: "json"` | `response_format: {type: json_object}` (retry without it if the server rejects it) | none: ask for JSON in the prompt, parse first `{` to last `}` |
| Output limit | optional `num_predict` | usually omit (newer models want `max_completion_tokens`) | `max_tokens` required: make it a setting, lower it if the model says it allows less |
| Model list (Test connection) | `GET /api/tags` | `GET {url}/models` | `GET /v1/models` |
| Usage | `prompt_eval_count`, `eval_count`, `total_duration` | `usage.prompt_tokens`, `completion_tokens` | `usage.input_tokens` (+ cache reads/writes), `output_tokens` |

Rules that made it robust:

- **Read the provider, address, model and key at each request**, so a settings change applies
  to the next request.
- **Errors say what to do**: 401/403 "the provider refused the access key — check it on App
  settings"; 404 "check the address and the model name"; unreachable/timeout name the provider
  and address. Include the provider's own error message; never the key.
- **Retry 429, 500, 502, 503, 504, 529** up to 3 times, waiting `Retry-After` (capped) or
  5 / 15 / 45 s. Make the sleep a function tests can replace.
- **Local-only rituals stay local**: a warm-up "hi" makes sense for Ollama (it loads the model);
  for a cloud provider it only costs money, so skip it.
- **Optional cheaper "text model"** for non-vision work (categorizing, writing SQL).
- **Test connection** lists the provider's models using what's typed on the page (a blank key
  box means the saved key). It generates nothing, so it's free and never competes with a
  running job.
- **Keep the one-job-at-a-time lock** whatever the provider; it keeps behaviour predictable.
- **Record every request** (purpose, model, tokens in/out, seconds, error) in a thread-local
  list the job wraps around its work, store it on the job's row as JSON, and show it on debug
  pages with a cost column from per-million prices set on the settings page (0 = local, no cost).

### The access key

- Setting kind `secret`: the page **never renders it** — an empty password box with
  "A key is saved (…last 4)". Blank box = keep; a "Remove the saved key" checkbox = clear.
- Only ever sent in a request header. Never in a URL, a log line, an error message, a page.
- **Database download blanks it** in the copy; **database import keeps the key the install
  already has** (the backup has none).

## 3. Tell people where their data goes

- Decide "outside the home network" from the configured address: an IP that isn't
  private/loopback/link-local, or a hostname that isn't `localhost`, single-label, or
  `.local .lan .home .internal .localdomain .home.arpa` (plus reserved `.localhost .test .invalid`).
- While it's outside, show a plain notice **to everyone** on Home, the settings page and every
  upload page: what is sent (page images, descriptions…), to which host, and that the provider's
  own privacy/retention rules apply. Admins get a link to the settings page.
- One template partial (`_ai_privacy.html`) and one template global (`ai_privacy()`), included
  wherever it's needed.

## 4. Make region- or vendor-specific parts optional

Finance had pieces tuned to one region (a local electricity and water utility, a toll road). For others:

- **Feature switches** on the settings page (`feature_utilities`, `feature_tolls`).
- **New installs start with them off; upgrades keep them on if there's data** — a migration:
  `INSERT OR IGNORE INTO app_settings (key, value) SELECT 'feature_x', '1' WHERE EXISTS (SELECT 1 FROM x_table);`
- **Off = invisible, not deleted**: a middleware puts `request.state.features` on every request;
  nav items and tab macros skip what's off; the feature's routers get
  `APIRouter(dependencies=[Depends(features.required("x"))])`, which raises an exception that a
  handler turns into a friendly 404 "X is turned off" page (with a link to settings for admins).
- **A generic path next to the tuned ones.** Keep the tuned prompts/parsers for the vendors you
  know, and add "Other…" with a free-text name: a general prompt, and the result **always waits
  for a person to confirm**, with "Re-extract" + "what was wrong?" notes sent back to the model.
- Watch database CHECK constraints and hard-coded lists (provider filters, dropdowns) that
  assumed only your vendors.

## 5. A fresh install has to be able to start

- With `admin_users` empty nobody is admin, so nobody can open the settings page. Show on
  **every page**: "No admin yet — add your Home Assistant user name (`<their name>`) to
  admin_users in the app's Configuration tab, save, restart." Link to the "How the app sees
  you" page (see `HA_ADDON_PATTERNS.md` section 4). Don't auto-promote the first visitor.
- Everything else starts empty and says so where it matters ("AI isn't set up — …" on the
  upload pages, pointing at the settings page).
- `panel_admin: false` in `config.yaml`, or only HA admins see the sidebar entry.

## 6. Package it as an app repository

Layout Supervisor reads (the app folder can have any name):

```
repository.yaml              name, url (https), maintainer
README.md                    for people browsing GitHub
<app>/                     e.g. app-source/
  config.yaml                name, version, slug, description, url, arch, ingress: true,
                             panel_admin: false, no ports:, options + schema (only the essentials)
  Dockerfile                 multi-arch base image (e.g. python:3.13-slim-...) → HA builds on install
  README.md                  store page: 3–5 sentences
  DOCS.md                    Documentation tab: the user guide (setup + what the app does)
  icon.png                   128×128
  logo.png                   250×100
  translations/en.yaml       configuration: <option>: {name, description}
  .dockerignore              keep tests, *.md (except README), icon/logo/translations out of the image
```

- `url` in `repository.yaml` and `config.yaml` is the **https** GitHub address (people add
  that in HA). Your own SSH remote is separate and never appears in these files.
- In this repository the Dockerfile follows `common/build/Dockerfile.template`, the pins are
  `common/build/requirements-base.txt`, and `python tools/check_build.py` checks every app's
  Dockerfile, requirements and `.dockerignore` (the shared copies under `app/common/` and
  `app/static/common/` must reach the image).
- Bump `version` every release or Supervisor won't offer the update.
- "Build on install" (no prebuilt images) is the simplest: nothing to maintain; first install
  takes a few minutes. Prebuilt images need a GitHub Actions builder and `image:` in config.
- Icon/logo: draw your own (a few lines of Pillow is enough); don't reuse brand marks.

## 7. Scrub anything personal — and keep it scrubbed

- Search the tracked files for your name, email, LAN addresses, account/plate/tag numbers,
  real statements. Replace examples with neutral ones (`jane.doe`, `192.168.1.10`).
- **Add a test** that greps every tracked text file for those strings, so they can't creep back:

  ```python
  files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
  for rel in files:
      ... for needle in ("your.name", "192.168.1.23", "your.email@"): assert needle not in text
  ```
- Test fixtures must be invented data (say so in the fixture's docstring).
- Also test the packaging: `repository.yaml` has name/url/maintainer; `config.yaml` has
  ingress, `panel_admin: false`, no `ports`, options == schema == translations keys, empty
  defaults; icon and logo sizes; README/DOCS exist.

## 8. Publish a clean copy, keep your working repository private

Your working repository's history has your email on every commit and old versions of files
you've since cleaned. Don't push it. Instead:

```bash
cd <working repo>
echo "public/" >> .gitignore                      # the public copy lives inside, ignored
mkdir -p public/<repo-name>
git archive HEAD | tar -x -C public/<repo-name>    # tracked files only: no backups, no scratch
cd public/<repo-name>
git init -b main
git config user.name  <github-user>
git config user.email <id+github-user>@users.noreply.github.com   # exact one: GitHub → Settings → Emails
git add -A && git commit -m "<App> 1.0.0"
git tag -a v1.0.0 -m "<App> 1.0.0"
git remote add origin git@github.com:<github-user>/<repo-name>.git
git push -u origin main --tags                     # --force only if the new GitHub repo has a README
```

The working repository keeps **no remote**, so it can't be pushed by mistake.

**Each later release:** work and commit in the working repository, bump `version`, then copy
the tracked files into `public/<repo-name>` (`git archive HEAD | tar -x -C public/<repo-name>`
after clearing it, or rsync), commit there with a short release message, tag, push.

## 9. Documentation for a 1.0 release

- **DOCS.md** (app Documentation tab) is the user guide: getting started (add repository,
  install, set admin, set up AI), privacy and cost, then each section of the app in plain
  language, the app options, and limits.
- **REQUIREMENTS.md** stays as the developer reference (current behaviour only, section numbers
  cited from code comments).
- **STATUS.md**: current version, layout, what's verified (and what isn't — e.g. "cloud
  providers tested against faked responses only"), known limitations, how to work on it.
- Drop the changelog at 1.0; git history keeps it.

## 10. Checklist

- [ ] Only bootstrap-critical app options (admins, trusted IPs), empty defaults, translations
- [ ] In-app settings page (admin), read on every use, validation, "last changed by"
- [ ] AI: provider choice, one client, secret key handling, test connection, errors + retries, usage/cost
- [ ] Privacy notice when the AI (or any service) is outside the home network
- [ ] Region/vendor parts behind feature switches (off for new installs, on for upgrades with data)
- [ ] Generic "Other…" path with human confirmation and re-extract notes
- [ ] First-run "No admin yet" notice; `panel_admin: false`; ingress only, no ports
- [ ] repository.yaml, config.yaml `url`/description, README, DOCS, icon, logo, .dockerignore
- [ ] Nothing personal in tracked files — enforced by a test; invented fixtures
- [ ] Packaging test
- [ ] Clean public copy (single commit, no-reply email, SSH remote); working repo has no remote
- [ ] Version bump, tag, push
