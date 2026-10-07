# Household Assistant — the app as built

The design is `HOUSEHOLD_ASSISTANT_SPEC.md` at the repository root (what a person sees, the bus kinds, trust and
privacy); the apps' side is `common/python/assist_tools.py`. This file says how this app does it.

## Layout

- `app/config.py` — options (`admin_users` only), Home Assistant's time zone, `DB_PATH` = `/data/assistant.db`.
- `app/db.py` — the schema (below), backups.
- `app/auth.py` — the ingress identity; `users` row on every request; admins from `admin_users` (id or login
  name). No "act as".
- `app/settings.py` — App settings (`settings_core` registry): AI (`ai_provider`, `ai_url`, `ai_model`,
  `ai_api_key` secret, `ai_max_tokens`), `children_may_ask` (off), `questions_per_hour` (30), `questions_per_day`
  (200), `question_timeout` (300 s, 60–900), `keep_days` (30, 1–365), `recorder_excluded` (off), `shared_open` (off).
- `app/ai_client.py` — the shared `common/ai_client.py` with this app's wording; `generate()` is the only way to the
  model (tests replace it).
- `app/catalogue.py` — the tools the apps offer (`assist.tools.list`), per person.
- `app/engine.py` — plan → call → answer; actions; the page's view of a question; housekeeping.
- `app/app_messages.py` — starts the bus (`can` is empty: the app answers no kinds).
- `app/routers/api.py` — the API (below). `app/main.py` — start-up, jobs, the ingress guard, CSP.
- `app/static/` — `index.html`, `app.js`, `style.css` and the shared `common/` files (no inline scripts).

## Data (`/data/assistant.db`)

- `users (id, name, enabled, is_child, created_at)`.
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
  tools. Tools that aren't well formed are left out and counted in `error`. A nack is kept as `error` ("The tool
  list is too big." for `answer too large`, "Didn't answer." when it expired).
- `for_user`: tools of apps with `enabled` and `app_on`, heard from in 24 hours, tool `enabled`; for a child only
  `scope: person` tools.

## A question

- `POST /api/ask` → `engine.ask`: 403 when the person can't ask (`users.enabled` off; a child while *Children
  may ask* is off; the AI not set up), 422 for an empty or too long question (1000 characters), 429 while one of
  theirs is still running (younger than 5 minutes), over *Questions per person per hour*, or over *Questions per
  day* (the household; `usage_days`). Then a thread answers it.
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
     model or the tool and its arguments in words); a round that only proposed is followed by another plan, so
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
  person, if the tool is still offered to them (403 otherwise; 409 when already done; 404 for someone else's).
- `GET /api/ask/{id}/events` → Server-Sent Events: `event: question` with the same view each time it changes (the
  engine's `touch(qid)` on every state change, warm-up, call sent and answer; checked again every second anyway),
  until the question ends; `: ping` every 20 s while quiet.
- `POST /api/ask/{id}/stop` → state `stopped` at once; the thread ends at its next check.

## API

| Route | Who | |
|---|---|---|
| `GET /api/me` | user | `user`, `canAsk`, `why`, `noAdmin`, `suggestions` (one example per app, else four defaults), `apps`, `privacy` (a cloud provider), `sharedOpen`, `recorderWarning` (admins, until ticked), `version` |
| `GET /api/whoami` | user | the shared whoami contract |
| `GET /api/tools` | user | the person's tools in words |
| `POST /api/ask` · `GET /api/ask/{id}` · `GET /api/ask/{id}/events` · `POST /api/ask/{id}/stop` · `POST /api/ask/{id}/act/{cid}` | owner | above |
| `GET /api/history?before=&limit=` · `DELETE /api/history` | user | own questions, newest first; clear (not a running one) |
| `GET/PUT /api/admin/settings` · `POST /api/admin/settings/test-ai` | admin | App settings; Test connection |
| `GET /api/admin/tools` · `POST /api/admin/tools/refresh` · `PUT /api/admin/tools/{app}` `{enabled}` | admin | Apps |
| `GET /api/admin/people` · `PUT /api/admin/people/{id}` `{enabled?, isChild?}` | admin | People (not oneself off) |
| `GET /api/admin/usage` · `GET /api/admin/connected-apps` | admin | Usage; Connected apps |
| `GET /api/admin-storage-download-db` · `POST /api/admin-storage-import-db` | admin | backups without the AI key; restore keeps this install's key |

Every route is behind the ingress guard and the shared cross-site check; the page's CSP allows scripts only
from the app.

## The page

One column: the conversation (question; answer as Markdown built as DOM nodes — paragraphs, lists, bold; never
HTML), proposed actions as buttons, **Sources** chips (opened with `ConnectedApps.openAppPage`), **What was
shared** (`<details>`), the suggestions and the ask box (Enter sends, Shift+Enter a new line, 🎤 where the browser
has speech recognition; 🔊 on an answer reads it with `speechSynthesis`, and a question asked with 🎤 is read aloud
when done). It follows a running question live (`/events`; polling every second where the stream can't be opened). `?q=` (on the page or the sidebar page's
address) fills in the question. Admins get Admin: Apps (with Connected apps), App settings (`settings.js`, with
Test connection), People, Usage, Storage.

## Jobs

- Every minute: `catalogue.check()`.
- Every hour: `engine.housekeeping()` — questions older than *Keep questions for* (their calls go with them),
  questions left running by a restart → failed, calls left `sent` over 2 minutes → timeout, usage older than
  400 days.

## Tests

`tests/test_assistant.py` runs the real app against a fake household (`tests/fakes.py`): fake Todo, Finance and
Vault apps, each a real `AppBus` with a real `assist_tools.Catalogue`, on one in-process bus, and a scripted
model. `tests/test_packaging.py`, `tests/test_security_headers.py` and the shared `tests/common_tests`.
