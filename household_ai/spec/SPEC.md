# Household AI — spec

Status: **built as 1.0.0 (2026-10-07), steps 1–3 of §8; not yet run on real hardware** (the image build, its size,
Ollama's library paths in the pinned image and each AI app's *Test connection* against it are still to check —
§8). Revised after review the same day (§9). Moved out of
`HOUSEHOLD_ASSISTANT_SPEC.md` (where it was drafted as a "Built-in model"; §15 there now points here) into an app of its own. A new app, `household_ai`, that runs an AI model on the Home Assistant machine's
**CPU**, with no GPU and no other server: Ollama, started inside the app's container, behind a small gateway. The
Household Assistant and every other household app with AI use it as an ordinary Ollama or OpenAI-compatible
address in their existing AI settings. **No other app's code changes.**

## 1. Purpose, and what it is not

- A household that wants AI but runs only Home Assistant (no second machine) today has to install a community
  Ollama app and wire every app to it by hand. Household AI is that model server made for these apps: models
  chosen with the RAM and disk in front of you, one model in memory for everyone (two Ollamas would load two copies
  of a model), a fair queue, and a day/night schedule for keeping models loaded.
- **A separate app, not part of the assistant**: the assistant (built as `household_assistant` 1.0.0) and every
  other app stay small Alpine images; only this app carries Ollama's bigger Debian-based image and
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
  machine's RAM (total / free, from `/proc/meminfo`), CPU cores, the CPU temperature where the machine reports one
  (`/sys/class/thermal`; a note when it is near the throttling point, as a Raspberry Pi without a fan soon is under
  a model), free disk under `/data` and whether models live on an SD card, and what is loaded now,
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
  row per app that has used it — *name (the app's host name, §4), last used, requests and tokens this month*, and
  an *Answer first* tick per row (§6.1). The values recommend the **Ollama** provider (§5).
- **Require an access key** (off): when on, the rows become keys — *label, created, last used, usage, Revoke* — and
  **New key** shows a key once (§4).
- **When models stay loaded** (§6.2): *Keep loaded* model and its daytime hours, *Unload after* for day and night,
  *Load at the start of the day*, *Models loaded at once*.
- **Limits** (§6.1): threads, context length, longest answer, longest run, queue length.
- **On my network**: not a switch. Home Assistant publishes an app's ports only from the app's own **Network** tab,
  which an app can't change; this section says whether port 11434 is published now (from the Supervisor's info
  about this app, §3), explains how to publish or unpublish it, and shows the standing warning of §4 when it is
  published with keys off.
- **Log**: the last 50 lines of the model server's own log.

Everything is set on this page (the shared `settings_core` registry), as in the other apps; the Configuration tab
holds only `admin_users`.

## 3. How it runs

- **The image.** The Ollama server binary and its **CPU** libraries only (no CUDA/ROCm, which are most of Ollama's
  own image). Ollama is built against glibc, so this app's image is `python:3.13-slim-trixie` (Debian, as Finance and
  Receipt), not the Alpine the other apps use; the Ollama files come from a pinned `FROM ollama/ollama:<version> AS ollama` stage
  (`COPY --from=ollama` of the binary and the top level of `/usr/lib/ollama`: since 0.40 a model runs in a separate
  `llama-server` process found there, with its libraries and the CPU backends; the GPU backends are in subfolders
  and are left out). The page reports a missing `llama-server` instead of failing on the first request. The final `FROM` stays a pinned `python:3.x`, so `tools/check_build.py`
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
  its own `ingress_port`. `config.yaml` declares the port as `ports: {11434/tcp: null}` with a
  `ports_description` — declared, so the Network tab offers it, but **not published** (`null`) until the admin
  enters a host port there (§4).
- **Storage**: models live in `/data/models` and are **left out of backups** with `backup_exclude` in
  `config.yaml` (a 5 GB model in every nightly backup would fill the disk). The Supervisor matches these as globs,
  so the entry is `["models", "models/**"]`, or whatever form a real backup proves: step 1 of §8 takes a backup
  with a model downloaded and checks its size, and a test keeps that entry in
  `config.yaml`. After a restore the page lists the
  models the settings name that are missing, with **Download again**. Free disk under 2 GB more than a download's
  size refuses the download with the numbers.
- **Memory**: the Supervisor doesn't cap an app's RAM; Linux's OOM killer may stop Ollama (or, worse, something
  else) if a model is too big. The app sees Ollama exit with a kill signal and says "The model server was stopped
  for lack of memory — choose a smaller model or unload sooner".
- **Home Assistant access**: `homeassistant_api: true` for the time zone (`ha_time`, for the daytime window) and the
  household apps bus (§5.1); `hassio_api: true` with the default role, used only for `GET /addons/self/info` (is
  11434 published on the LAN, §2). This is the first household app with `hassio_api` on; the README and
  `SECURITY.md` say why. Nothing else.
- **Internet**: downloads come from Ollama's registry (`registry.ollama.ai`); a machine without internet, or a
  failed download, gets the registry's error in plain words. A cancelled or failed download's partial files are
  left to the model server, which removes unused files the next time it starts. Before a download the app reads
  the model's size from the registry's manifest (the recommended table's size when that fails). Answering needs no
  internet.
- **Watchdog**: `watchdog: http://[HOST]:[PORT:11434]/api/version`, so the Supervisor restarts the app if the
  gateway itself stops answering (the gateway answers `/api/version` itself while Ollama is off or restarting).

## 4. Who can reach it (the gateway)

Ollama has no authentication and its API can download and delete models, so it is never exposed raw. The gateway
on port 11434:

- **Allows only** `GET /api/version`, `GET /api/tags`, `POST /api/show`, `POST /api/generate`, `POST /api/chat`,
  `POST /api/embed`, and the OpenAI-compatible `GET /v1/models`, `POST /v1/chat/completions`. `GET /api/version`
  needs no key even with keys on (the Supervisor's watchdog asks it) and is answered while the model server is
  off. Everything else
  (`/api/pull`, `/api/delete`, `/api/create`, `/api/copy`, `/api/push`, blobs) is 404 — model management is only on
  the admin page, behind ingress and the admin check.
- **Checks every request** before queueing it: a body over 32 MB is 413 (a few photos as base64 fit); the
  caller's `keep_alive` is replaced by the schedule's (§6.2); `options.num_ctx` is capped at *Context length*,
  `options.num_thread` is set to *Threads*, and `options.num_predict` (`max_tokens` on `/v1`) is capped at *Longest
  answer* (default 2048 tokens) — so no caller can load a model with a context that fills the RAM, or keep the CPU
  generating without end. `/api/tags` and `/v1/models` list only downloaded models (and, with keys on, only those
  the key may use).
- **No key by default.** The model is on the Supervisor's internal network only, with no outside access, so
  *Require an access key* is **off**: any app there may use it, and the other apps' *Access key* field stays empty.
  The allow-list above still holds without a key, so no caller can download, delete or replace models.
- **Telling callers apart without keys.** Each app container has its own address on the internal network, but the
  address changes when an app restarts, and the Supervisor's DNS isn't relied on to answer reverse lookups. So the
  gateway works **forwards**: every household app's host name is this app's own repository prefix plus the app's
  slug (Household AI is `a1b2c3d4-household-ai`, so the assistant is `a1b2c3d4-household-assistant`), and the
  gateway resolves the known household slugs (the assistant, Calorie Tracker, Docs, Arcade, Receipt, Finance) every
  minute and on start-up, and matches a request's address against them. A caller that matches none (a community
  app, a LAN machine) is shown and counted by its address, as "Other app (172.30.33.7)". Usage is recorded under
  the name, so an app's usage survives its restarts. A reverse lookup is tried as a second source when building
  shows it works; nothing depends on it. Usage, *Answer first* and the per-caller queue limit (§6.1) work per
  caller the same way.
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
  `config.yaml` declares 11434 but leaves it **unpublished** by default (§3). To use the model from other machines
  on the LAN, the admin enters a host port for it on the app's **Network** tab in Home Assistant (the page explains
  how; the app can't publish it itself). Published with *Require an access key* off, the page shows a standing
  warning ("Anyone on your network can use the model and its CPU"); the page offers to turn keys
  on, and says plain HTTP on the LAN shows a key to anyone listening.
- **Other apps on the internal network** (community apps too) can use the model without a key while keys are off —
  the accepted trade-off of the default; they still can't manage models, and the queue limits them like any app.
- **Logging**: per caller (key, or host name/address), per request: time, model, path, input/output tokens,
  seconds, queue wait; never prompts, images or answers. Tokens come from Ollama's `prompt_eval_count` /
  `eval_count` (native) or `usage` (`/v1`; for a streamed `/v1` request the gateway adds
  `stream_options: {include_usage: true}` and drops that last usage chunk if the caller didn't ask for it). Kept
  30 days, shown on the page as usage per caller per day.

## 5. Pointing an app at it

The values the page shows, for the app's existing *Admin → App settings → AI* block:

| Field | Value |
|---|---|
| Provider | **Ollama** (recommended; *OpenAI-compatible* works too — then the address ends in `/v1`) |
| Address | `http://<Household AI's host name>:11434` (the page fills in the real host name) |
| Access key | empty (or the new key, when *Require an access key* is on) |
| Model / Vision model | one of the downloaded models (*Test connection* lists them) |

- **Which apps**: Household Assistant (text; `HOUSEHOLD_ASSISTANT_SPEC.md` §15), Calorie Tracker, Household Docs,
  Household Arcade (text), Receipt Price Intelligence and Finance Dashboard (vision; slow, §6). Each app's own
  timeout settings matter, because **an app's timeout covers the time its request waits in the queue as well as
  the answer** (the shared `ai_client` sends `stream: false`, so nothing arrives until the answer is done):
  Receipt's *timeout* should be raised to at least 300 s for a CPU vision model, and *receipts read at the same
  time* set to 1; an app whose requests wait behind a vision job needs a timeout longer than one such job. The
  page lists each app's suggested timeout next to its row.
- **Why Ollama over `/v1`**: the native routes carry `keep_alive` and `options`, so the schedule and the limits
  apply exactly; `/v1` requests get the same caps where the OpenAI form has a field for them (`max_tokens`), and
  the schedule by the empty request of §6.2.
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
  Assistant (recognised by its forward-resolved host name, §4), because a person is waiting on the page; then
  everyone in arrival order. The admin can tick other callers on the page. A request already running is never
  interrupted.
- **No limit on waiting time.** A waiting request stays in the queue until its turn, however long the request
  ahead of it runs (a vision job can take minutes); a fixed wait limit would turn the assistant away behind every
  receipt. The bound on waiting is the **caller's own timeout** (§5): when a caller gives up and closes the
  connection, the gateway notices and drops its request from the queue, so it never runs for nobody.
- **Queue length** (default 4 waiting, setting): beyond it the gateway answers **503 with `Retry-After`** — the
  running job's expected time left, from the model's recent seconds per request, between 10 and 60 s — which the
  shared `ai_client` already retries (its `RETRY_STATUSES`, up to 3 times, waits capped at 60 s). Each caller may
  have at most 2 requests waiting, so one app reading a pile of receipts can't starve the others; a third is 503
  the same way.
- **A caller that goes away while its answer runs** (closed connection) has the Ollama request cancelled at once,
  so the CPU isn't spent on an answer nobody will read.
- **Longest run** (default 15 min, setting): a request still running after it is cancelled and answered 504, so a
  stuck generation can't hold the CPU for everyone.
- **Model switches**: by default one model is loaded at a time (*Models loaded at once* 1); a request for another
  model waits for the running one, then loads (a few seconds from an SSD, up to a minute from an SD card). The page
  suggests one text model for everything text and one vision model, and shows when switching is frequent ("Loaded
  40 times today — consider using one model in more apps"). *Models loaded at once* may be 2 when the RAM allows
  both (the page adds up the two models' RAM and warns when that leaves Home Assistant under 4 GB).
- **Threads** (default: all cores but one, at least 1) and **Context length** (default 8192) are settings. Ollama
  has no server-wide thread setting, so the gateway sets `options.num_thread` on every native request (§4);
  `/v1` requests have no `options`, so they run with Ollama's own default (all physical cores) — one more reason
  the page recommends the Ollama provider. *Context length* is also Ollama's `OLLAMA_CONTEXT_LENGTH` (§3), and the
  gateway caps a caller's larger `num_ctx` at it.

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
  `day_unload_min` (10; applies only to models other than the kept one — the kept one never unloads in the
  daytime, which is the table's *Never* in §6.2), `night_unload_min` (10; 0 = right after each answer), `preload`
  (true), `max_loaded` (1; 1–2), `context` (8192; 2048–32768), `max_answer_tokens` (2048; 256–8192),
  `max_run_min` (15; 1–60), `queue` (4), `answer_first` (list of caller names, default
  `["household_assistant"]`), `require_key` (false).
- Tables in `/data/household_ai.db` (`model_keys` is used only with keys on; `caller` is a key id, or the app's
  slug when keys are off and §4 matched it, else `addr:<address>`):

```sql
CREATE TABLE model_keys (id TEXT PRIMARY KEY, label TEXT NOT NULL, key_hash TEXT NOT NULL UNIQUE,
  models TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT);
CREATE TABLE model_usage (day TEXT NOT NULL, caller TEXT NOT NULL, model TEXT NOT NULL, requests INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, seconds REAL NOT NULL, waited REAL NOT NULL,
  PRIMARY KEY (day, caller, model));
CREATE TABLE model_callers (caller TEXT PRIMARY KEY, address TEXT, first_seen_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL);       -- the "Use it in other apps" rows; forgotten after 30 days unused
-- plus app_settings, and bus_outbox / bus_seen / bus_apps (app_bus.migrate) once §5.1 is built
```

  Keys are hashes, so backups carry no usable key; restoring a backup keeps the keys working (same hashes). Usage
  older than 30 days is deleted by housekeeping.
- API (ingress, admin only): `GET /api/status` (state, machine, loaded models, schedule), `PUT /api/server`
  (on/off), `GET /api/models`, `POST /api/models/pull` `{name}` → progress via `GET /api/models/pull/{id}`,
  `DELETE /api/models/pull/{id}` (cancel), `DELETE /api/models/{name}`, `POST /api/unload`, `GET /api/usage`,
  `POST /api/keys` `{label, models?}` → `{key}` once, `DELETE /api/keys/{id}`, `GET /api/keys`, `GET /api/log`,
  `PUT /api/models/keep` `{name}` (*Keep loaded*; `""` = the first text model, `none`), `GET /api/callers` (the
  values to paste and the callers seen), `PUT /api/callers/{caller}/answer-first` `{on}`, `GET/PUT
  /api/admin/settings`, `GET /api/me`, `GET /api/whoami`. All behind the
  ingress check, the admin check and the cross-site guard; the shared App settings, backups (database only),
  themes and *How the app sees you* as every app has them.

## 8. Build plan

Steps 1–3 are built (1.0.0); the hardware checks in steps 1 and 3 are still to do.

1. **Image and skeleton**: the Ollama stage, `python:3.13-slim-trixie`, `backup_exclude`, `arch` amd64/aarch64,
   `panel_admin: true`; the shared skeleton (ingress identity, settings, backups, themes). `check_build.py` needed
   no change: it checks the last `FROM`. **To do on hardware:** build the image, check the copied Ollama
   paths (`/usr/bin/ollama`, `/usr/lib/ollama/llama-server` and its libraries in 0.40.0 — the first build copied
   only the `libggml` libraries and every answer failed with "llama-server binary not found"), measure the image size (target under 600 MB) and a cold start.
2. **The model server**: the child process (start, watch, restart, stop), the page (status, machine, models,
   pull/delete, log), the day/night schedule (§6.2). Tests with a fake `ollama serve` (a tiny HTTP server answering
   the few routes) and a fake clock.
3. **The gateway** on 11434: allow-list, request checks and caps, callers by forward-resolved host names, the
   optional keys, the queue with *Answer first* and per-caller limits, cancellation, `keep_alive`, usage. Tests:
   without keys any internal caller is served and `/api/pull` is still 404; with keys a key-less and a revoked key
   are refused; a third waiting request from one caller is 503; the assistant jumps the queue; a request waiting
   behind a 5-minute job is not turned away; a caller that disconnects while waiting is dropped, and one that
   disconnects while running has its Ollama request cancelled; a run past *Longest run* is 504; `num_ctx` and
   `num_predict` are capped and `num_thread` set; an app restarting on a new address keeps its name and usage; a
   `/v1` request resets the timer and its tokens are counted. Then check each AI app's
   *Test connection* and one real request against it (Ollama provider and `/v1`), on amd64 and on a Raspberry Pi 5.
4. **Later**: `ai.server` discovery (§5.1) and a *Use Household AI* button in the shared AI block; embeddings for
   Docs' search through `/api/embed`.

Size: about Calorie Tracker's (≈1,500 lines of Python, ≈700 of page, ≈2,000 of tests); the Household Assistant
(already built) needs nothing from it beyond an Ollama address.

## 9. Decisions

- **Its own app, not inside the assistant** (decided 2026-10-07, reversing the first draft): the model server is
  shared by every AI app, not the assistant's; keeping it apart leaves the assistant a small Alpine app,
  puts the Debian image and its heavier updates in one optional place, and lets each be
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
- **No limit on waiting time** (decided 2026-10-07, replacing a 120 s limit): a vision job can run for minutes,
  and a fixed limit would turn the assistant away behind every receipt. The caller's own timeout bounds the wait,
  and a request whose caller has gone is dropped (or cancelled, if running); *Longest run* bounds a stuck job.
- **Callers by forward-resolved host names, not by address or reverse DNS** (2026-10-07): addresses change when
  an app restarts and reverse lookups on the Supervisor network aren't assured; the household apps' host names
  follow from this app's own and are resolved forwards every minute.
- **`/v1` passed through, not translated to the native API** (2026-10-07): translating would give `/v1` callers
  the thread setting and exact `keep_alive`, but means re-implementing OpenAI's message, image and tool formats.
  The page recommends the Ollama provider instead; every household app already supports it.
- **`hassio_api` for one call** (2026-10-07): only to read whether 11434 is published, so the page's LAN warning
  is true rather than guessed.
- **No GPU support in the first release**: Home Assistant OS machines rarely have one usable from an app; the
  gateway and settings would not change if a GPU build were added later.
