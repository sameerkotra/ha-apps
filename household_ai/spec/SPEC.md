# Household AI — spec

Status: **draft (2026-10-07), not built** (only this spec exists in `household_ai/` so far). Moved out of
`HOUSEHOLD_ASSISTANT_SPEC.md` (where it was §14, "Built-in
model") into an app of its own. A new app, `household_ai`, that runs an AI model on the Home Assistant machine's
**CPU**, with no GPU and no other server: Ollama, started inside the app's container, behind a small gateway. The
Household Assistant and every other household app with AI use it as an ordinary Ollama or OpenAI-compatible
address in their existing AI settings. **No other app's code changes.**

## 1. Purpose, and what it is not

- A household that wants AI but runs only Home Assistant (no second machine) today has to install a community
  Ollama app and wire every app to it by hand. Household AI is that model server made for these apps: models
  chosen with the RAM and disk in front of you, one model in memory for everyone (two Ollamas would load two copies
  of a model), a fair queue, and a day/night schedule for keeping models loaded.
- **A separate app, not part of the assistant**: the assistant (and every other app) stays a small Alpine image
  that installs everywhere, including 32-bit systems; only this app carries Ollama's bigger Debian-based image and
  is 64-bit only (§3). A household that already has an Ollama, a cloud provider or Claude simply doesn't install
  it. And the model server can be restarted, updated or turned off without touching the assistant.
- **Not** a general model server for the network: by default it is reachable only by other apps on the
  Supervisor's internal network, with no access key needed there (§4). Opening it to the LAN, and requiring a key,
  are explicit admin choices.
- **Not** fast. CPU-only means seconds for a short text answer from a 3B model and from about 30 seconds to several
  minutes for a receipt photo or a statement page from a vision model (§6). The page says so before the first
  download.
- In the README's table it is listed with "—" in the AI column: it gives AI to the other apps rather than needing
  one.

## 2. What an admin sees

The app's page is for **admins only** (`panel_admin: true`; the Supervisor shows it in the sidebar to Home
Assistant administrators). The rest of the household uses it only through the other apps. Sections:

- **Status.** The model server's state (*Starting* · *Ready* · *Stopped — <reason>*), the Ollama version, the
  machine's RAM (total / free, from `/proc/meminfo`), CPU cores, free disk under `/data`, and what is loaded now,
  since when and until when ("`qwen2.5:3b` — kept until 22:00, then unloads 10 min after its last use"), with
  **Unload now** (until the next request, or the next day's start). **Off / On** stops and starts the server.
- **Models.** A short **recommended list** shipped with the app (name, download size, RAM needed, *Text* or
  *Vision*, which apps it suits), each with **Download** / **Delete**, plus *Other model…* for any Ollama model name.
  Download shows progress (Ollama's `/api/pull` stream) and can be cancelled. First-release list (sizes checked at
  build time and kept in one table in code):

  | Model | Kind | Approx. RAM | Suits |
  |---|---|---|---|
  | `qwen2.5:1.5b` | Text | ~1.5 GB | the assistant on a Raspberry Pi; Docs, Calorie, Arcade extras |
  | `qwen2.5:3b` (**default suggestion**) | Text | ~2.5 GB | the assistant (JSON plan); Docs, Calorie, Arcade extras; Finance's optional text model |
  | `llama3.2:3b` | Text | ~2.5 GB | the same, an alternative |
  | `qwen2.5vl:3b` | Vision | ~4 GB | Receipt Price Intelligence, Docs' text from scans; slow on CPU |
  | `qwen2.5vl:7b` | Vision | ~7 GB | Finance Dashboard statements, Receipt (better reading); 16 GB machines |

  A model whose RAM need is more than the machine's free RAM plus what the server already holds gets a warning
  ("This model needs about 7 GB; this machine has 3.1 GB free — Home Assistant may slow down or the app may be
  stopped"), not a block.
- **Use it in other apps**: the exact values to paste into another app's *Admin → App settings → AI* (§5), and one
  row per app that has used it — *host name (or address), last used, requests and tokens this month*.
- **Require an access key** (off): when on, the rows become keys — *label, created, last used, usage, Revoke* — and
  **New key** shows a key once (§4).
- **When models stay loaded** (§6.2): *Keep loaded* model and its daytime hours, *Unload after* for day and night,
  *Load at the start of the day*, *Models loaded at once*.
- **Limits** (§6.1): threads, context length, queue length, *Answer first*.
- **Show on my network** (off): the LAN switch (§4).
- **Log**: the last 50 lines of the model server's own log.

Everything is set on this page (the shared `settings_core` registry), as in the other apps; the Configuration tab
holds only `admin_users`.

## 3. How it runs

- **The image.** The Ollama server binary and its **CPU** libraries only (no CUDA/ROCm, which are most of Ollama's
  own image). Ollama is built against glibc, so this app's image is `python:3.12-slim` (Debian), not the Alpine the
  other apps use; the Ollama files come from a pinned `FROM ollama/ollama:<version> AS ollama` stage
  (`COPY --from=ollama` of the binary and `lib/ollama` CPU backends — the exact paths checked when building, since
  Ollama has moved them between releases). The final `FROM` stays a pinned `python:3.x`, so `tools/check_build.py`
  passes; the Ollama version is pinned and bumped with the app's version, with a CHANGELOG line.
- **Architectures**: Ollama ships amd64 and arm64 only, so `arch` is `amd64` and `aarch64`.
- **A child process**, started by the app's lifespan when the server is on: `ollama serve` under `nice -n 10` (Home
  Assistant itself stays responsive while a model works), listening on **`127.0.0.1:11435` only**, with
  `OLLAMA_MODELS=/data/models`, `OLLAMA_MAX_LOADED_MODELS` from *Models loaded at once*, `OLLAMA_NUM_PARALLEL=1`,
  `OLLAMA_KEEP_ALIVE` from the night *Unload after* (the gateway sets each request's own, §6.2),
  `OLLAMA_CONTEXT_LENGTH` from *Context length*, `OLLAMA_NO_CLOUD`/telemetry off where the version has it. The app
  watches it (`/api/version` every 30 s), restarts it on exit with 5 s → 5 min back-off, and stops it with SIGTERM
  on shutdown (10 s, then SIGKILL).
- **The gateway.** The app serves the model to other apps on **port 11434**, through a small gateway in Python
  (§4); Ollama is never reachable from outside the container directly. The admin page is the usual ingress page on
  its own `ingress_port`.
- **Storage**: models live in `/data/models` and are **left out of backups** with `backup_exclude: ["models"]` in
  `config.yaml` (a 5 GB model in every nightly backup would fill the disk). After a restore the page lists the
  models the settings name that are missing, with **Download again**. Free disk under 2 GB more than a download's
  size refuses the download with the numbers.
- **Memory**: the Supervisor doesn't cap an app's RAM; Linux's OOM killer may stop Ollama (or, worse, something
  else) if a model is too big. The app sees Ollama exit with a kill signal and says "The model server was stopped
  for lack of memory — choose a smaller model or unload sooner".
- **Home Assistant access**: `homeassistant_api: true` for the time zone (`ha_time`, for the daytime window) and the
  household apps bus (§5.1); nothing else.

## 4. Who can reach it (the gateway)

Ollama has no authentication and its API can download and delete models, so it is never exposed raw. The gateway
on port 11434:

- **Allows only** `GET /api/version`, `GET /api/tags`, `POST /api/show`, `POST /api/generate`, `POST /api/chat`,
  `POST /api/embed`, and the OpenAI-compatible `GET /v1/models`, `POST /v1/chat/completions`. Everything else
  (`/api/pull`, `/api/delete`, `/api/create`, `/api/copy`, `/api/push`, blobs) is 404 — model management is only on
  the admin page, behind ingress and the admin check.
- **No key by default.** The model is on the Supervisor's internal network only, with no outside access, so
  *Require an access key* is **off**: any app there may use it, and the other apps' *Access key* field stays empty.
  The allow-list above still holds without a key, so no caller can download, delete or replace models. Callers are
  told apart by their address on the internal network (each app container has its own); the gateway shows the
  app's host name for it where the Supervisor's DNS gives one back (checked when building), else the address.
  Usage, *Answer first* and the per-caller queue limit (§6.1) work per caller the same way.
- **Require an access key** (setting, off): then every request needs `Authorization: Bearer <key>` — 32 random bytes
  (base64url), shown once, stored as a SHA-256 hash with its label; no key or a wrong one → 401 (10 failures a
  minute from one address → 429 for a minute), and callers are told apart by key. The shared `ai_client` already
  sends `Authorization: Bearer` for both the Ollama and the OpenAI-compatible providers when an access key is set
  (`auth_headers`), so the other apps need no change either way. Turn it on if a community app you don't trust is
  installed, or when the model is shown on the network.
- **Models**: a request for a model that isn't downloaded is 404 with "Download <model> in Household AI first" (no
  pull on demand). With keys on, an admin may limit a key to some models.
- **Where it listens**: the Supervisor's internal network, where every app reaches another by its host name — the
  full slug with `_` turned into `-` (APP_MESSAGES_SPEC §6.5), e.g. `http://a1b2c3d4-household-ai:11434`.
  `config.yaml` has **no** published port by default. *Show on my network* is the app's `ports:` entry
  `11434/tcp: null`, which the admin sets on the app's **Network** tab in Home Assistant (the page explains how);
  then other machines on the LAN can use it too. Turning *Show on my network* on with *Require an access key* off
  shows a standing warning ("Anyone on your network can use the model and its CPU"); the page offers to turn keys
  on, and says plain HTTP on the LAN shows a key to anyone listening.
- **Other apps on the internal network** (community apps too) can use the model without a key while keys are off —
  the accepted trade-off of the default; they still can't manage models, and the queue limits them like any app.
- **Logging**: per caller (key, or host name/address), per request: time, model, path, input/output tokens,
  seconds, queue wait; never prompts, images or answers. Kept 30 days, shown on the page as usage per caller per
  day.

## 5. Pointing an app at it

The values the page shows, for the app's existing *Admin → App settings → AI* block:

| Field | Value |
|---|---|
| Provider | **Ollama** (or *OpenAI-compatible* — then the address ends in `/v1`) |
| Address | `http://<Household AI's host name>:11434` (the page fills in the real host name) |
| Access key | empty (or the new key, when *Require an access key* is on) |
| Model / Vision model | one of the downloaded models (*Test connection* lists them) |

- **Which apps**: Household Assistant (text; `HOUSEHOLD_ASSISTANT_SPEC.md` §14), Calorie Tracker, Household Docs,
  Household Arcade (text), Receipt Price Intelligence and Finance Dashboard (vision; slow, §6). Each app's own
  timeout settings matter: Receipt's *timeout* should be raised to at least 300 s for a CPU vision model, and
  *receipts read at the same time* set to 1; the page lists this.
- **Turning it off** stops the server; other apps then get "unreachable" from their own *Test connection* and AI
  buttons, as with any stopped Ollama. With keys on, revoking a key makes that app's requests 401 at once.

### 5.1 Discovery over the bus (later, step 4 of §8)

Household AI joins the household apps bus (`app_bus`, `APP_MESSAGES_SPEC.md`) and lists `ai.server` in its `hello`
`can`. An app may send `ai.server` `{}` → `ack {result: {address, models: [{name, kind}], openai_path: "/v1",
key_needed}}` — the address and model names only, **never a key** (the bus is readable by HA admins and every app
with `homeassistant_api`, APP_MESSAGES_SPEC §8). An app's AI block can then show "Household AI is installed — Use
it", filling Provider, Address and the model list — all that's needed while keys are off (with keys on, the admin
still pastes the key). That is a change to the shared `settings.js` / each app's AI block and is not needed for the
first release; `APP_MESSAGES_SPEC.md` gets the kind when it is built.

## 6. Sharing one CPU

### 6.1 The queue

One model answers one request at a time (`OLLAMA_NUM_PARALLEL=1`); a vision request can hold the CPU for minutes. So
the gateway, not Ollama, queues:

- **Answer first** (setting): callers whose waiting requests go ahead of the others — by default the Household
  Assistant (recognised by its host name, `*-household-assistant`), because a person is waiting on the page; then
  everyone in arrival order. A request already running is never interrupted.
- **Queue length** (default 4 waiting, setting): beyond it, and for any request that has waited 120 s, the gateway
  answers **503 with `Retry-After: 30`**, which the shared `ai_client` already retries (its `RETRY_STATUSES`). Each
  caller may have at most 2 requests waiting, so one app reading a pile of receipts can't starve the others.
- **Model switches**: by default one model is loaded at a time (*Models loaded at once* 1); a request for another
  model waits for the running one, then loads (a few seconds from an SSD, up to a minute from an SD card). The page
  suggests one text model for everything text and one vision model, and shows when switching is frequent ("Loaded
  40 times today — consider using one model in more apps"). *Models loaded at once* may be 2 when the RAM allows
  both (the page adds up the two models' RAM and warns when that leaves Home Assistant under 4 GB).
- **Threads** (default: all cores but one, at least 1) and **Context length** (default 8192) are settings.

### 6.2 When models stay loaded (day and night)

Loading costs seconds to a minute; keeping a model loaded costs its RAM. So an admin sets a daytime window when the
household asks most, and a shorter rule for the rest:

| Setting | Default | What it does |
|---|---|---|
| *Keep loaded* | the first text model downloaded | The model that stays in memory during the daytime window (*None*: no model is kept). |
| *Daytime* | **05:00–22:00** | In Home Assistant's time zone (`ha_time`), so it follows daylight saving. A window that passes midnight (22:00–06:00) is allowed. |
| *During the day, unload after* | **Never** (the *Keep loaded* model); 10 min (any other model) | How long after its last use a model is unloaded in the daytime. |
| *At night, unload after* | **10 min** | Every model, the kept one included, unloads this long after its last use outside the window (0 = right after each answer). |
| *Load at the start of the day* | on | At the window's start, the *Keep loaded* model is loaded before anyone asks, so the first question of the morning doesn't wait for it. |

- **How it is done.** Ollama keeps a model loaded for the `keep_alive` of the last request that used it. The gateway
  sets `keep_alive` on every native request it passes on (`-1` for the kept model in the day, otherwise the minutes
  above); for `/v1` requests, which don't carry it, the gateway sends Ollama an empty
  `POST /api/generate {model, keep_alive}` after the answer, which only resets the timer. At the window's start
  (with *Load at the start of the day*) the same empty request with `keep_alive: -1` loads the kept model; at the
  window's end the gateway sends it again with the night value, so a model idle for longer is unloaded at once and
  one in use unloads that long after its last request. The clock is checked every minute and at start-up, so a
  restart mid-day loads the kept model again, and a restart at night loads nothing.
- **Other models** never displace the kept one when *Models loaded at once* is 2: a vision request loads the vision
  model next to it and the vision model unloads by its own rule. With 1, the vision request unloads the kept model,
  and the gateway loads it back after the vision model's last request, in the daytime only.
- **Example: 16 GB RAM, SSD.** *Keep loaded* `qwen2.5:3b` (~3 GB) 05:00–22:00, *Models loaded at once* 2,
  `qwen2.5vl:7b` (~7 GB) for receipts and statements unloading 10 minutes after use, night 10 minutes. In the day
  about 3 GB is always in use and 10 GB while a receipt is being read; Home Assistant keeps 6 GB or more. A load
  from the SSD is a few seconds, so the night rule costs little.

## 7. Settings, data and API

- Settings (Admin → App settings, `settings_core`): `server_on` (false), `threads` (0 = automatic), `keep_model`
  (empty = the first text model downloaded; `none` = no model kept), `day_start` (`05:00`), `day_end` (`22:00`),
  `day_unload_min` (10; for models other than the kept one, which stays loaded), `night_unload_min` (10; 0 = right
  after each answer), `preload` (true), `max_loaded` (1; 1–2), `context` (8192; 2048–32768), `queue` (4),
  `answer_first` (`household_assistant`), `require_key` (false).
- Tables in `/data/household_ai.db` (`model_keys` is used only with keys on; `caller` is a key id, or the host name
  or address when keys are off):

```sql
CREATE TABLE model_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  models TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT);
CREATE TABLE model_usage (day TEXT NOT NULL, caller TEXT NOT NULL, model TEXT NOT NULL, requests INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, seconds REAL NOT NULL, waited REAL NOT NULL,
  PRIMARY KEY (day, caller, model));
-- plus app_settings, and bus_outbox / bus_seen / bus_apps (app_bus.migrate) once §5.1 is built
```

  Keys are hashes, so backups carry no usable key; restoring a backup keeps the keys working (same hashes). Usage
  older than 30 days is deleted by housekeeping.
- API (ingress, admin only): `GET /api/status` (state, machine, loaded models, schedule), `PUT /api/server`
  (on/off), `GET /api/models`, `POST /api/models/pull` `{name}` → progress via `GET /api/models/pull/{id}`,
  `DELETE /api/models/pull/{id}` (cancel), `DELETE /api/models/{name}`, `POST /api/unload`, `GET /api/usage`,
  `POST /api/keys` `{label, models?}` → `{key}` once, `DELETE /api/keys/{id}`, `GET /api/log`. All behind the
  ingress check, the admin check and the cross-site guard; the shared App settings, backups (database only),
  themes and *How the app sees you* as every app has them.

## 8. Build plan

1. **Image and skeleton**: the Ollama stage, `python:3.12-slim`, `backup_exclude`, `arch` amd64/aarch64,
   `panel_admin: true`; a `check_build.py` allowance for this one app's multi-stage image; the shared skeleton
   (ingress identity, settings, backups, themes). Measure the image size (target under 600 MB) and a cold start.
2. **The model server**: the child process (start, watch, restart, stop), the page (status, machine, models,
   pull/delete, log), the day/night schedule (§6.2). Tests with a fake `ollama serve` (a tiny HTTP server answering
   the few routes) and a fake clock.
3. **The gateway** on 11434: allow-list, callers by address (and the host-name lookup), the optional keys, the
   queue with *Answer first* and per-caller limits, `keep_alive`, usage. Tests: without keys any internal caller is
   served and `/api/pull` is still 404; with keys a key-less and a revoked key are refused; a third waiting request
   from one caller is 503; the assistant jumps the queue; a `/v1` request resets the timer. Then check each AI app's
   *Test connection* and one real request against it (Ollama provider and `/v1`), on amd64 and on a Raspberry Pi 5.
4. **Later**: `ai.server` discovery (§5.1) and a *Use Household AI* button in the shared AI block; embeddings for
   Docs' search through `/api/embed`.

Size: about Calorie Tracker's (≈1,500 lines of Python, ≈700 of page, ≈2,000 of tests); it can be built before,
after or alongside the Household Assistant, which needs nothing from it beyond an Ollama address.

## 9. Decisions

- **Its own app, not inside the assistant** (decided 2026-10-07, reversing the first draft): the model server is
  shared by every AI app, not the assistant's; keeping it apart leaves the assistant a small Alpine app that runs
  everywhere, puts the 64-bit-only Debian image and its heavier updates in one optional place, and lets each be
  restarted or updated alone. The cost: one more app to install, and the assistant reaches its model over the
  internal network rather than `127.0.0.1` (a millisecond, next to seconds of model time).
- **A gateway, not raw Ollama on the internal network**: any community app can reach the internal network, and raw
  Ollama would let it pull or delete models and use the CPU without limit. The gateway's allow-list and queue stop
  that with or without keys.
- **No key by default** (decided 2026-10-07): the model has no outside access, and an internal network shared only
  with the household's own apps doesn't need one; pasting keys into every app was friction for no real gain. Keys
  stay as a switch for a household that installs untrusted apps or shows the model on its LAN.
- **Keys (when on) pasted, not sent over the bus**: the bus is readable by HA admins and every app with
  `homeassistant_api`; a key there is a key for all of them.
- **Off by default, no automatic downloads**: a model is gigabytes of disk and RAM on the machine that runs the
  home; the admin chooses with the numbers in front of them.
- **No GPU support in the first release**: Home Assistant OS machines rarely have one usable from an app; the
  gateway and settings would not change if a GPU build were added later.
