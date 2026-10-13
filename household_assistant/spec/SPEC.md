# Household Assistant — the app as built

The design is `HOUSEHOLD_ASSISTANT_SPEC.md` at the repository root (what a person sees, the bus kinds, trust and
privacy); the apps' side is `common/python/assist_tools.py`. This file says how this app does it.

## Layout

- `app/config.py` — options (`admin_users` only), Home Assistant's time zone, `DB_PATH` = `/data/assistant.db`.
- `app/db.py` — the schema (below), backups.
- `app/auth.py` — the ingress identity (a plain dependency, run in the threadpool): the `users` row is read on every
  request and written only when it is new (`reviewed` 0) or the name changed; admins from `admin_users` (id or
  login name). No "act as".
- `app/settings.py` — App settings (`settings_core` registry): AI (`ai_provider`, `ai_url`, `ai_model`,
  `ai_api_key` secret, `ai_max_tokens`), `children_may_ask` (off), `questions_per_hour` (30), `questions_per_day`
  (200), `question_timeout` (300 s, 60–900), `keep_days` (30, 1–365), `recorder_excluded` (off), `shared_open` (off),
  `briefing_speakers` ("" = all; media players, comma-separated), `quiet_from` / `quiet_until` (HH:MM, "" = never
  quiet), `bus_signing_key` (secret, "" = unsigned; `clear_bus_signing_key`). A saved `ai_api_key` is kept only
  while the provider and the address's scheme, host and port stay the same: changing them without a new key (or
  `clear_ai_api_key`) is a 422, and Test connection answers "Type the access key again" instead of sending it.
- `app/ai_client.py` — the shared `common/ai_client.py` with this app's wording; `generate()` is the only way to the
  model (tests replace it). Each call's `deadline` (retries included) is what is left of the question (the engine
  passes it); by default the larger of its timeout and the shared 300 s.
- `app/catalogue.py` — the tools the apps offer (`assist.tools.list`), per person.
- `app/engine.py` — plan → call → answer; actions; the page's view of a question; housekeeping.
- `app/app_messages.py` — starts the bus with `signing_key` (App settings → `bus_signing_key`, read at most every
  5 s); it answers `assist.ask`.
- `app/routers/api.py` — the API (below). `app/main.py` — start-up, jobs, the ingress guard, CSP.
- `app/static/` — `index.html`, `app.js`, `style.css` and the shared `common/` files (no inline scripts).

## Data (`/data/assistant.db`)

- `users (id, name, enabled, is_child, created_at, reviewed)` — `reviewed` 0: new (Admin → People shows it; any
  change there or `{reviewed: true}` sets it); rows from before the column count as reviewed.
- `rate_events (user_id, kind question|briefing, at)` — what the hourly limits count; not deleted with the
  questions; pruned after a day.
- `questions (id, user_id, asked_at, text, answer, state planning|calling|answering|done|failed|stopped, error,
  rounds, input_tokens, output_tokens, seconds)`.
- `calls (id, question_id → questions ON DELETE CASCADE, app, tool, args, say, bus_id, round, confirmed_at,
  sent_at, answered_at, state proposed|sent|ok|nack|timeout, result, reason)` — a proposed action is a `calls`
  row with `say` and state `proposed` until tapped. `reason` is `<nack reason>[:<detail>]`, or `not_available` /
  `not_sent` when the call couldn't leave.
- `assist_apps (app, name, app_version, learned_at, app_on, enabled, error)` — `app_on` is the app's own switch
  (from its tool list's `on`), `enabled` the admin's switch here.
- `assist_tools (app, name, app_version, spec, learned_at, enabled)` — `spec` is the cleaned tool (name, what,
  args, returns, acts, scope, examples).
- `usage_days (day, questions, calls, input_tokens, output_tokens)` — UTC days, kept 400 days, never per person.
- `app_settings`, and the bus's `bus_outbox`, `bus_seen`, `bus_apps`.

## The catalogue

- `refresh(apps)` sends `assist.tools.list` (expires in 2 minutes, `ref` `tools:<slug>`) to every app whose hello
  lists the kind and that was heard from in the last 24 hours; never `household_vault`. `check()` runs every
  minute and asks an app with no list yet, a new version since its list, or a list over a day old.
- The answer replaces the app's tools; each tool is checked (`clean_tool`): the name `area.thing[.verb]`, never
  the `vault` area, at most 8 arguments of the known types (an enum needs values), texts shortened, at most 24
  tools; `scope` is `person` only when the tool says so, else `household`. Tools that aren't well formed are left
  out and counted in `error`. The household's own apps (`KNOWN_AREAS`: `household_todo` → `todo`, …) may offer
  tools only in their area, and no other app in one of those areas (left out, in `error`). A new `assist_apps` row
  starts `enabled` for those apps and off for any other. A nack is kept as `error` ("The tool
  list is too big." for `answer too large`, "Didn't answer." when it expired).
- `for_user`: tools of apps with `enabled` and `app_on`, heard from in 24 hours, tool `enabled`; a name that more
  than one app offers is offered by neither; for a child, or a question asked `person_only` (Assist's `scope:
  person`), only `scope: person` tools.

## A question

- `POST /api/ask` → `engine.ask`: 403 when the person can't ask (`users.enabled` off; a child while *Children
  may ask* is off; the AI not set up), 422 for an empty or too long question (1000 characters), 429 while one of
  theirs is still running (younger than 5 minutes, or its thread hasn't returned yet — also after Stop), over
  *Questions per person per hour* (`rate_events`), or over *Questions per day* (the household; `usage_days`). The
  question's Stop is registered before its row is committed (so housekeeping never takes it for one left by a
  restart); then a thread answers it.
- Warm-up: with Ollama, when the model hasn't answered for 4 minutes, `warmup_sync()` sends "hi" first (up to 120 s; progress "Waking up the model…"); the question's clock starts after it.
- The loop (at most 3 rounds, *Longest a question may take* in all — `question_timeout`, 300 s, 60–900; `Stopped` / `TooLong` checked between steps; a `TooLong` or `AIError` after some apps answered ends the question
  `done` with the apps' own `text`s, one line each, under a line saying the model didn't finish):
  1. **Plan**, with *Tool calls* "auto" (the default) and a model that takes them: `generate_tools()` — the
     shared `Client.tool_call` (Ollama `/api/chat` tools, OpenAI `tools`, Claude `tools`) — with the person's
     catalogue as native tools (`tool_specs`: `todo.tasks` → `todo__tasks`, flat arguments as JSON Schema, the
     app's name and CHANGES DATA in the description) and `native_prompt` (no tool list or format; with results,
     the answer rules, so words in reply are the final answer and there is no separate answer step). A 400
     (no tool support) is remembered for that provider, address and model until the AI settings change, and that
     round and later ones use the JSON plan. Otherwise (*Tool calls* "json"):
     `generate(want_json=True)` with the system prompt (§3.1, the person's name, today's date and time
     zone), the tools as one line each (name, app, CHANGES DATA for `acts`, what, arguments with type, values,
     range, required, returns), the last 6 done questions and answers, the results so far in a `<data>` block,
     the proposed changes, the reply format and the question. `parse_plan` takes the first JSON object:
     `call` (a list or one), `act` (a list or one, at most 3), `answer`, `ask`. Text that isn't JSON, before any
     result, is taken as the answer (small models answer greetings in words).
  2. `ask` → that is the answer. `act`, or a `call` of an `acts` tool → a proposal (`calls` row, `say` from the
     model or the tool and its arguments in words; at most 3 a question). The button is `label` — `describe()`: "<app> ·
     <tool> (<arg>: <value>, …)", made by the app — with the model's `say` only as a note under it; a round that only proposed is followed by another plan, so
     the model can say it in words. Calls of unknown tools, repeated calls and arguments the tool doesn't have
     are dropped; at most 4 a round and 8 a question.
  3. **Call**, in parallel: one `assist.tool.call` each (`requested_by` the person, `question` the id; expires in
     20 seconds; `ref` `ask:<call id>`), waiting up to 20 seconds; the answer comes through `bus.on_reply("ask:")`,
     which stores it and wakes the waiting call. No answer → `timeout`; never sent again.
  4. **Answer**: when there were results (or proposals), `generate()` in plain text with the rules (results only,
     say what is missing, never invent, short, Markdown lists and bold only, name the app, no links). Results go
     to the model as one `<data>` block of at most 24 KB (each result's text, items and "more"; a refusal in
     words).
- `GET /api/ask/{id}` (`engine.view`): state, answer, error, `progress` ("Thinking…", "Asking Household Todo…",
  "Reading the answer…"), `sources` (the apps' own links, checked by `safe_link`: `panel` must be
  `/<8 hex or local>_<that app's slug>`, `target` a plain path without `//` or `..`; else the app's name only;
  at most 8), `shared` (each call: app, tool, args, state, text, items, links, `problem` in words) and `actions`.
- `POST /api/ask/{id}/act/{call id}` → the proposal is sent with `confirm: true` and `confirmed_at`, as the
  person, if they may still ask (`can_ask`) and the tool is still offered to them (403 otherwise; 409 when already
  done — only the request that moves the row from `proposed` to `sent` sends it; 404 for someone else's).
- `GET /api/ask/{id}/events` → Server-Sent Events: `event: question` with the same view each time it changes (the
  engine's `touch(qid)` on every state change, warm-up, call sent and answer sets an `asyncio.Event` of each stream
  watching it, via `call_soon_threadsafe`; the stream reads the question only then, or after 20 quiet seconds,
  when it also sends `: ping`), until the question ends. At most 6 streams per person (429). Versions are kept only
  while the question's thread runs or a stream watches it.
- `POST /api/ask/{id}/stop` → state `stopped` at once; the thread ends at its next check.

## API

| Route | Who | |
|---|---|---|
| `GET /api/me` | user | `user`, `canAsk`, `why`, `noAdmin`, `suggestions` (one example per app, else four defaults), `apps`, `privacy` (a cloud provider), `sharedOpen`, `recorderWarning` (admins, until ticked), `version` |
| `GET /api/whoami` | user | the shared whoami contract |
| `GET /api/tools` | user | the person's tools in words |
| `POST /api/ask` · `GET /api/ask/{id}` · `GET /api/ask/{id}/events` · `POST /api/ask/{id}/stop` · `POST /api/ask/{id}/act/{cid}` | owner | above |
| `GET /api/history?before=&limit=` · `DELETE /api/history` | user | own questions, newest first; clear (not a running one) |
| `GET /api/briefing` · `PUT /api/briefing {on, time, days, eveningOn?, eveningTime?, speaker?}` · `POST /api/briefing/send?kind=morning\|evening` | user | the person's morning and evening briefings and speaker (HOUSEHOLD_ASSISTANT_SPEC §16); only the speakers the admin allows; `quiet` (the admin's quiet hours). Send: the slot is taken first (`briefing.reserve`, one write transaction; 429 when 5 this hour); `quiet: true` when not read aloud for quiet hours |
| `GET/PUT /api/admin/settings` · `POST /api/admin/settings/test-ai` | admin | App settings; Test connection |
| `GET /api/admin/tools` · `POST /api/admin/tools/refresh` · `PUT /api/admin/tools/{app}` `{enabled}` | admin | Apps |
| `GET /api/admin/people` · `PUT /api/admin/people/{id}` `{enabled?, isChild?, reviewed?}` | admin | People (not oneself off); `new` until reviewed, new ones first |
| `GET /api/admin/usage` · `GET /api/admin/connected-apps` | admin | Usage; Connected apps |
| `GET /api/admin-storage-download-db` · `POST /api/admin-storage-import-db` | admin | backups without the AI key; restore keeps this install's key |

Every route is behind the ingress guard and the shared cross-site check; the page's CSP allows scripts only
from the app. Request bodies: 1 MB (`web_security.BodyLimit`), the restore 1 GB with a Content-Length. Files
asked for with `?v=` are cached for good (`static_immutable`); the page links every file with the app's version.
`GET /api/history` builds its page with `engine.views` (the apps' names and the calls read once).

## The page

One column: the ask box at the top (Enter sends, Shift+Enter a new line, 🎤 where the browser has speech
recognition), *What can I ask?*, *🌅 Briefings* (a dialog: morning and evening each on and at a time, every day / weekdays,
what each includes, the phones it goes to, a speaker to read it aloud, *Send a morning / an evening one now*) and *Clear my questions*, the suggestions, then the conversation, newest first
(*Earlier questions* at the bottom): each question, its answer as Markdown built as DOM nodes — paragraphs, lists,
bold; never HTML — proposed actions as buttons, **Sources** chips (opened with `ConnectedApps.openAppPage`) and
**What was shared** (`<details>`); 🔊 on an answer reads it with `speechSynthesis`, and a question asked with 🎤 is
read aloud when done. It follows a running question live (`/events`; polling every second where the stream can't
be opened). `?q=` (on the page or the sidebar page's address) fills in the question. Admins get Admin: Apps (with Connected apps), App settings (`settings.js`, with
Test connection), People, Usage, Storage.

## Assist (the companion integration)

`custom_components/household_assistant` (repository root; HACS via `hacs.json`) is a Home Assistant integration: a
config entry, a `conversation` entity (the agent), options *Answer voice satellites as* (an HA user) and *Wait for
an answer* (300 s; 30–900). `protocol.py` (no Home Assistant imports) builds the messages; on the bus it is
`ha_assist`. Its options also have *Voice: household-scope tools off* (`voice_person_only`: a turn without a user
is asked with `scope: "person"`) and the *App-messages signing key* (`signing_key`: every message it fires is
signed with the shared app_bus HMAC — `protocol.sign` — and replies without a valid `sig` are ignored). It says
hello at start and to the app when the app says hello or `who` (at most once a minute); a `nack busy` is asked
once more after 2 s; on Home Assistant with chat logs it answers through `_async_handle_message` and sets
`continue_conversation` when the answer is a question back; a Repairs issue (`recorder_keeps_messages`) while the
recorder doesn't exclude `household_apps`:

- `assist.ask` (`{requested_by, name?, text, scope?: "person"}`, expires in 5 minutes; ref `assist:<id>`) →
  `app_messages.on_ask`:
  `nack not_allowed sender` unless it is from `ha_assist`, `nack not_allowed off` while *Answer Assist*
  (`assist_answers`) is off, `nack invalid <field>`; else `ack {}` and, after the bus commits (the bus holds the
  database), `auth.user_by_id` (the person's users row; made only when Home Assistant's people list links that user
  id to a person — any other unknown id is refused, "I don't know who is asking…") and `engine.ask` — the same checks and limits as the page; a refusal becomes a `failed` answer.
- `engine.when_done(qid, fn)` → `assist.answer` as a reply to the ask (`reply_to`, same `ref`, 5 minutes):
  `{question, state, answer? (≤ 3000), error?, sources: [app names]}`; the integration acks it, and its outbox
  re-sends it until then.
- The integration waits 15 s for the `ack` ("The Household Assistant app isn't answering…"), then up to its wait
  for the answer, and speaks it without Markdown (`protocol.plain`). A turn without a user (a voice satellite)
  is asked as the chosen person, or answered "I don't know who is asking…".

`integration_tests/` runs it in a real Home Assistant core (pytest-homeassistant-custom-component);
`tests/test_assistant.py` runs `protocol.py` against this app.

## Jobs

- Every minute: `catalogue.check()`, `briefing.check()` (the morning and evening briefings that are due).
- Every 5 minutes: the people and their phones from Home Assistant (the shared `ha_people.loop`).
- Every hour: `engine.housekeeping()` — questions older than *Keep questions for* (their calls go with them),
  questions left running by a restart (older than 30 s, no thread) → failed, calls left `sent` over 2 minutes →
  timeout, usage older than 400 days, `rate_events` older than a day; and download copies
  (`household-assistant-*.db` in the temp folder) older than 6 hours (`backup_core.remove_stale`).
- Briefings: due from their time until 3 hours after it, also past midnight (`due_day`: at 00:30 a 23:00 one is
  the previous day's, marked as that day's); a scheduled one counts in `rate_events` too; never read aloud during
  the admin's quiet hours.

## Tests

`tests/test_assistant.py` runs the real app against a fake household (`tests/fakes.py`): fake Todo, Finance and
Vault apps, each a real `AppBus` with a real `assist_tools.Catalogue`, on one in-process bus, and a scripted
model. `tests/test_review_oct13.py` the October 13 review's regressions. `tests/test_briefing.py` the morning briefing on the same household (with Finance's `finance.bills`);
`tests/test_page_browser.py` the page, its dialog included, in headless Chromium. `tests/test_packaging.py`, `tests/test_security_headers.py` and the shared `tests/common_tests`.
