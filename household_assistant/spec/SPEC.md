# Household Assistant — the app as built

The design is `HOUSEHOLD_ASSISTANT_SPEC.md` at the repository root (what a person sees, the bus kinds, trust and
privacy); the apps' side is `common/python/assist_tools.py`. This file says how this app does it.

## Layout

- `app/config.py` — options (`admin_users` only), Home Assistant's time zone, `DB_PATH` = `/data/assistant.db`.
- `app/db.py` — the schema (below), backups.
- `app/auth.py` — the ingress identity (a plain dependency, run in the threadpool): the `users` row is read on every
  request and written only when it is new (`reviewed` 0) or the name changed; admins from `admin_users` (id or
  login name). No "act as".
- `app/settings.py` — App settings (`settings_core` registry): AI (`ai_on` — Use AI, `ai_provider`, `ai_url`, `ai_model`,
  `ai_api_key` secret, `ai_max_tokens`), `children_may_ask` (off), `questions_per_hour` (30), `questions_per_day`
  (200), `question_timeout` (300 s, 60–900), `keep_days` (30, 1–365), `recorder_excluded` (off), `shared_open` (off),
  `briefing_speakers` ("" = all; media players, comma-separated), `quiet_from` / `quiet_until` (HH:MM, "" = never
  quiet), `bus_signing_key` (secret, "" = unsigned; `clear_bus_signing_key`). A saved `ai_api_key` is kept only
  while the provider and the address's scheme, host and port stay the same: changing them without a new key (or
  `clear_ai_api_key`) is a 422, and Test connection answers "Type the access key again" instead of sending it.
  Quick answers: `quick_on` (on), `quick_unsure` (`ai` | `choices`), `quick_learn_taps` (on), `quick_learn_ai`
  (on), `quick_ai_now` (off), `quick_learn_rephrase` (on), `quick_recent` (60 s, 0–600; 0 = never reuse),
  `quick_timeout` (5 s, 2–20). `quick_on` and `ai_on` can't both be off (422). The
  model's fields and the AI-learning ones are greyed out while `ai_on` is off (`enabled_if`).
- `app/ai_client.py` — the shared `common/ai_client.py` with this app's wording; `generate()` is the only way to the
  model (tests replace it). Each call's `deadline` (retries included) is what is left of the question (the engine
  passes it); by default the larger of its timeout and the shared 300 s.
- `app/catalogue.py` — the tools the apps offer (`assist.tools.list`), per person; a tool's `quick` block (the app's
  own words, §17.4) is kept, checked (`clean_quick`).
- `app/quick/` — quick answers without a model (HOUSEHOLD_ASSISTANT_SPEC.md §17, below).
- `app/engine.py` — plan → call → answer; actions; the page's view of a question; housekeeping.
- `app/app_messages.py` — starts the bus with `signing_key` (App settings → `bus_signing_key`, read at most every
  5 s); it answers `assist.ask`.
- `app/routers/api.py` — the API (below); `app/routers/people_data.py` — one person's data (below). `app/main.py` —
  start-up, jobs, the ingress guard, CSP.
- `app/static/` — `index.html`, `app.js`, `style.css` and the shared `common/` files (no inline scripts).

## Data (`/data/assistant.db`)

- `users (id, name, enabled, is_child, created_at, reviewed)` — `reviewed` 0: new (Admin → People shows it; any
  change there or `{reviewed: true}` sets it); rows from before the column count as reviewed.
- `rate_events (user_id, kind question|briefing, at)` — what the hourly limits count; not deleted with the
  questions; pruned after a day.
- `questions (id, user_id, asked_at, text, answer, state planning|calling|answering|done|failed|stopped, error,
  rounds, input_tokens, output_tokens, seconds, mode quick|ai, quick, understood)` — `quick` (JSON) is what the
  rules understood: `{kind, template, norm, intents: [{intent, label, calls, source, phrase, acts, note}], choices,
  chosen, suggest}`; `understood` the line above a quick answer.
- `quick_phrases (id, phrase, intent, args, slot_args, source admin|tapped|rephrased|ai, seen, hits, created_at,
  last_used)` — learned phrases by template (a table from before `rephrased` is rebuilt once, in one transaction);
  `quick_misses (phrase, count, last_seen)` — normalised words and a count only; `quick_words (word, means,
  created_at)` — the admin's words: a concept, or `<kind>:<name>` another name.
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
| `GET /api/me` | user | `user`, `canAsk`, `why`, `noAdmin`, `suggestions` (the quick questions the person may use, most used first; else one example per app, else four defaults), `apps`, `privacy` (a cloud provider, only while AI is in use), `sharedOpen`, `recorderWarning` (admins, until ticked), `ai {on, model}`, `quick`, `quickOnly`, `version` |
| `POST /api/ask/{id}/choose` `{choice}` · `POST /api/ask/{id}/not-this` · `GET /api/quick/suggestions` | owner · owner · user | Did you mean (the same question answered; learned) · Not what I asked (a new question: the model with a hint, or without AI the other meanings) · the one-tap questions |
| `POST /api/admin/quick/test` `{text}` · `GET /api/admin/quick/intents` · `GET/POST/DELETE /api/admin/quick/phrases[/{id}]` · `GET/DELETE /api/admin/quick/misses` | admin | Admin → Quick answers: how a question is read (calls nothing); the intents; phrases (never to an action; fixed values checked against the tool); misses |
| `GET/POST /api/admin/quick/words` `{word, means}` · `DELETE /api/admin/quick/words/{word}` · `GET /api/admin/quick/export` · `POST /api/admin/quick/import` · `GET /api/admin/quick/apps` · `GET /api/admin/quick/usage` · `POST /api/admin/quick/replay` | admin | the admin's words and the misses' suggestions; phrases and words as a JSON file, and merging one in; each app's speed (7 days); questions per person and the household's kinds (counts only); the last 100 quick answers read again |
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
| `GET/POST /api/admin/people/{id}/data[/export\|/import/{token}\|/delete]` · `POST /api/admin/people/data/import` · `GET /api/admin/people/data/jobs/{job}[/file]` | admin | one person's data (below) |

Every route is behind the ingress guard and the shared cross-site check; the page's CSP allows scripts only
from the app. Request bodies: 1 MB (`web_security.BodyLimit`), the restore 1 GB with a Content-Length, a person's export file 64 MB. Files
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

## Quick answers (`app/quick/`, HOUSEHOLD_ASSISTANT_SPEC.md §17)

- `normalise.py` — tokens (amounts, times, dates as one token), contractions, text-speak, number words, filler, word
  forms (a table; each token keeps `orig`, the word as typed, for a task's or an item's text); `correct()` puts typos
  right against the lexicon and the household's names (4 letters: two neighbours swapped; 5–7: one edit; 8+: two;
  never a known word, a name or a tie).
- `lexicon.py` — the concepts and their words (in Python, not YAML: no new dependency), everyday words the typo
  correction leaves alone, groceries, well-known stores, categories, meals, stop words, ordinals.
- `slots.py` — when (today/tomorrow/week/next week/overdue/all, a weekday, a date), days ahead (or back: "the last 7
  days"), a month (with or without a year; "may"/"march" only after in/for/…), a time of day ("at 5" is 17:00 up to
  7), amounts, other numbers.
- `match.py` — `read()`: concepts (phrases indexed by their first word; a hit inside a longer hit of another concept
  dropped), the household's names (`Vocab`), everyday items and stores; `template()` for learned phrases.
- `intents.py` — one rule per kind of question (the table in §17.6, plus `item_alone`, `checklist`, `scores`,
  `recent_split`, `merchant`, `food_log`, `task_done`, `reminder`…), each with must / helps / out and a builder; an
  app's own tool with a `quick` block and no rule gets a generic one.
- `understand.py` — learned phrase → an answer to the last Did you mean → the one-word table → a follow-up → scoring
  (threshold 2.5, margin 1.5×) → several questions in one. The question is read as typed first; with its typos
  put right only when that isn't understood, or when the corrected reading rules the first one out.
- `vocab.py` — the household's names per person, fetched in the background with the apps' own tools
  (`todo.lists`, `receipt.shopping_list`, `splitpot.balances`, `finance.summary`, `arcade.scores`) as the person, at
  most every 10 minutes per tool, in memory only.
- `learn.py` — `quick_phrases`, `quick_misses`, housekeeping (180 / 90 days).
- `engine.quick_answer()` runs first unless *Not what I asked* forced the model; `compose()` puts the apps' own
  texts together (several parts under their understood lines; "milk" adds whether it's on the list); actions become
  `proposed` calls. `engine.choose()` answers the same question with a choice; `engine.not_this()` asks again.
  `_learn_from_model()` stores a candidate after a model answer with exactly one read-only call.
- More (HOUSEHOLD_ASSISTANT_SPEC.md §17.15): Hindi/Hinglish in `normalise.py`; `slots.months`; `Utterance.kin`
  ("my mother", "Ravi's wife"); alias groups, names written together or apart, the apps' `aliases` and the admin's
  words (`Vocab.aliases`, `Vocab.words`); accounts from `finance.balances`; the intents `compare`, `shop_tick`,
  `task_move`, `expense_log`, `note_add`, `checklist_tick`, `checklist_add`; `Match.ask` (yes/no, count) and
  `engine._headline`; `engine._compared`; `engine._chained` (a checklist found by its name, then the change
  proposed); the reused answers `engine._recent` (`forget_recent` on any confirmed change and on a restore);
  `_learn_rephrasing`; habits in `engine.suggestions` (`why`, `/api/me` `suggestionWhy`); `engine.app_speeds`,
  `usage_by_person`, `kinds_asked`, `replay`; `learn.words` / `add_word` / `word_suggestions` / `export` /
  `import_`.
- **Use AI off** (`ai_on`): `engine.run` never reaches the model; `ai_client.check_configured` raises too, and
  `needs_warmup()` is false, so no path can call a model; Test connection answers without asking anything.
- The person is released (`_owners`) as soon as the answer is written, so the next question can follow at once.

## One person's data (`app/routers/people_data.py`, PERSON_DATA_SPEC.md §2 and §8)

Admin → People: a **⋯ Data** button per row (`PeopleData.menu`) and **Import a person's data…** above the table
(`PeopleData.importButton`; `cfg.people` = the People list, `onDone` reloads it). The file, its checks and the
dialogs are the shared `common/person_data.py` and `common/person-data.js`.

| Route (admin, the real signed-in one) | |
|---|---|
| `GET /api/admin/people/{id}/data` | the summary: questions kept (the oldest), the app answers behind them, briefings on (the speaker); the export option; what stays; what it can't reach; `deleteChoices: []`. Also the delete's dry run |
| `POST /api/admin/people/{id}/data/export` `{options: {questions}}` | the `.zip` at once (`Content-Disposition: attachment`, `person_data.file_name`); never a job — there are no files |
| `GET /api/admin/people/data/jobs/{job}[/file]` | the shared job API; always 404 here (no export is big) |
| `POST /api/admin/people/data/import` multipart `file` | `{token, preview}`; the file kept an hour (`Uploads`, `/data/household-assistant-person-up-*.zip`, also swept hourly); every row checked already, so the preview shows a refusal |
| `POST /api/admin/people/{id}/data/import/{token}` `{mode: add\|replace, confirm?}` | `{message, imported}`; Replace: 409 unless `confirm` is their name |
| `POST /api/admin/people/{id}/data/delete` `{confirm, forget}` | `{message, deleted, forgot}`; 409 unless `confirm` is their name |

- **Schema** (`people_data.SCHEMA`): **1** — the tables as they are now (users with `reviewed`, questions with
  `mode`/`quick`/`understood`, briefings with the evening and the speaker). A file with a higher number is refused;
  when a later update raises it, older files are brought up to date where the rows are read (`_rows`).
- **In the file**: `users` (id, name, `enabled`, `is_child`, `created_at` — information only, never loaded),
  `briefings` (every column but `user_id`), and only with **Include their questions and answers** (off) `questions`
  (every column but `user_id`) and their `calls` (every column). Never: `rate_events`, `quick_phrases`,
  `quick_misses`, `quick_words`, `usage_days`, the catalogue, `app_settings` (so no AI key or signing key), the bus's
  tables. The export's log line says whether questions were included.
- **Import**: every row through Pydantic models with the app's own limits (`MAX_QUESTION`, `MAX_ANSWER`, the
  briefing's `TIME_RE`/`DAYS`/`SPEAKER_RE`, states, JSON columns that must read as objects, times with a zone);
  unknown columns dropped; a row that doesn't fit refuses the file with its table and row number (400). Then, under
  `one_at_a_time` (423 when another import or delete runs), in one `BEGIN IMMEDIATE` transaction: Replace first
  deletes as below (without forgetting); briefings — **Add** keeps the person's row when they have one, **Replace**
  takes the file's; questions get new ULIDs (from their `asked_at`) and their calls new ids pointing at them
  (`IdMap`); a question with the same `asked_at` (normalised to the second, UTC) and text is a duplicate, skipped;
  one older than *Keep questions for* is left out (the preview counts them). A question still running in the file
  comes in `stopped`; a call `proposed` or `sent` comes in as `timeout` (a record — never a button that could send it
  again); `bus_id` is not kept. Nothing is added to `rate_events` or `usage_days`. `enabled`, `is_child` and
  `reviewed` are never imported. An unknown speaker is kept (the preview warns; the page says not found).
- **Delete**: their running questions are stopped (`engine.stop`; up to 5 s for the threads to end), then in one
  transaction their `calls`, `questions`, `briefings` and `rate_events`, and with **forget** their `users` row (the
  next request makes them new, `reviewed` 0); then `engine.forget_recent(id)` and `quick.vocab.forget(id)`. No files,
  no Home Assistant sensors.
- One log line (`people_data` logger) per export, import and delete: who (name and id), whom (id), the counts.

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
`tests/test_page_browser.py` the page, its dialog included, in headless Chromium. `tests/test_quick.py` the rules
alone: the phrase corpus `tests/data/quick_corpus.tsv` (every line, at least 25 per intent, near-misses, speed;
`python3 tests/quick_corpus.py` lists every miss) against every household tool (`tests/data/quick_tools.json`);
`tests/test_quick_flow.py` quick answers end to end on a fake household with a model that fails the test if it is
called. The model-path tests run with `quick_on` off (`fakes.set_up_ai`). `tests/test_person_data.py` one person's data (export, import, delete). `tests/test_packaging.py`, `tests/test_security_headers.py` and the shared `tests/common_tests`.
