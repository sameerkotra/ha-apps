# Household Arcade — how it works

Classic arcade games for everyone in a Home Assistant home, with personal
bests, a household leaderboard and play-time limits for children. This file
describes the app as it is now; the next build (playing together from two
phones, §13) and the games still to come are at the end.

## 1. Scope

- Twenty games, written from scratch (no ROMs, no emulators, no commercial
  names, artwork, music or level layouts): **Snake**, **Brick Breaker**,
  **Falling Blocks**, **Paddle Duel**, **Lane Racer**, **Flap**, **Mines**,
  **Merge**, **Colour Memory**, **Memory Cards**, **Tap the Mole**, **Number
  Dash**, **Tank Battle**, **Sky Defenders**, **Rocks**, **Road Hop**, **Sudoku**, **Word Guess**, **Word Search** and
  **Snake Duel** (two players on one screen, or against the computer).
- Every game except the three puzzle and word games (Sudoku, Word Guess, Word Search, made from the seed) has a level list that an AI model can add to (§11); the modes
  that play it are listed in `games.py` (`level_modes`). Admin → AI usage shows
  every request made to the model (§11.10).
- Everyone plays as their Home Assistant account; no extra accounts or PINs.
- Personal bests, the last 20 games, a household leaderboard (top 10, all time
  and this month), Practice games that aren't saved.
- Children: daily minutes, quiet hours, allowed games, leaderboard visibility,
  extra time, play history.
- Optional Home Assistant sensors and phone notifications, off by default.
- Works offline on every Home Assistant system (pure Python, static files).

## 2. Stack and files

FastAPI + uvicorn, raw `sqlite3`, plain HTML/CSS/JS with no build step and no
libraries. Ingress only.

```
config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  translations/en.yaml
app/
  main.py         lifespan (migrate, time zone, people, jobs), ingress source check, security headers (CSP), routers
  config.py       admin_users, DATA_DIR, version, Home Assistant time zone (config.ZONE, UTC fallback), now()/today()
  db.py           schema as numbered migrations, schema_version, backup/import (on common db_core / backup_core)
  auth.py         identity from X-Remote-User-*, require_admin (on common auth_core)
  settings.py     App settings declared on common settings_core (SETTINGS, GROUPS; cached, applied without restart)
  levels.py       level lists: built-in levels (level_data.py), checks, the levels table
  level_builder.py  building more levels with the AI model (ai_client.py)
  games.py        the server's game table: ids, names, modes, score limits; the looks
  limits.py       children: day type, quiet hours, time used/left, limits validation
  scores.py       saving, personal bests, records, leaderboard, retention
  notify.py       record notifications, limit warnings to parents
  ha_sensors.py   the optional sensors (on common sensor_publisher)
  housekeeping.py stale sessions, Keep scores for (loop = common housekeeping.periodic)
  ha_client.py    thin: re-exports the shared Core API client (app/common/ha_client.py) + load_timezone
  together.py     playing together: invites, matches, the live numbers, winners, head to head (§13)
  common/         shared Python (copies of the repository's common/): ha_notify, ha_people, whoami, ha_client,
                  ha_time, housekeeping, auth_core, db_core, settings_core, people_admin, web_security,
                  backup_core, sensor_publisher, ai_client
  routers/        me.py, prefs.py, play.py, users.py, admin.py, levels.py, together.py (+ the live WebSocket)
  static/
    index.html style.css app.js play.js admin.js together.js
    common/       shared browser files (copies): theme-boot.js themes.css ui.js settings.js settings.css
                  people.js backnav.js whoami.js
    games/        the games (see spec/GAMES.md): kit.js sound.js registry.js, then <game>-logic.js
                  (rules) and <game>.js (drawing) for each of the 17 games
  level_kinds/    one file per game (but Brick Breaker and Snake · Maze): its level format and checks (§11.9)
  level_common.py the level name check and LevelError, shared by levels.py and level_kinds
  ai_usage.py     every request to the AI model, and the AI usage report (§11.10)
tests/            unittest suite (python -m unittest discover -s tests); tests/js for the games (node --test)
  common_tests/   shared helpers (fake_ha, env, ingress, packaging_core) and the shared modules' tests (copies)
```

`app/common/`, `app/static/common/` and `tests/common_tests/` are written by `tools/sync_common.py` from
`common/manifest.json` (see `common/README.md`); never edit a copy (`tests/common_tests/test_shared_copies.py`
fails if one was changed).

## 3. Manifest and options

`slug: household_arcade`, `ingress: true`, `ingress_port: 8104`,
`panel_icon: mdi:gamepad-variant`, `panel_title: Arcade`, `panel_admin: false`,
no `ports:`, `homeassistant_api: true` (time zone, people, notifications,
sensors), every other API flag false, no `map:`. Architectures amd64, aarch64,
armv7, armhf, i386. The only option is `admin_users` (empty by default).
Database `/data/arcade.db`.

### 3.1 App settings (`settings.py`, Admin → App settings)

`GET/PUT /api/admin/settings` → the shared payload (`settings_core`)
`{values, defaults, meta, groups, secretsSet}` (`meta[key]` = label, help,
group, kind, range, choices, … — the page is drawn from it by
`common/settings.js`) plus `providers`, `games`, `looks`, `admins`, `hasToken`
and `timeZone`. Unknown keys or bad values are a 422 and nothing is saved.
Every key applies at once. The page's groups: Games, Looks and scores,
Children (school days, holidays, who gets warnings), Home Assistant, AI levels.

| Key | Default | |
|---|---|---|
| `disabled_games` | `[]` | game ids switched off (new games start on) |
| `default_look` | `modern` | |
| `brick_powerups` | `true` | off hides the `powerups` mode and refuses it |
| `leaderboard` | `true` | |
| `school_days` | `[1,2,3,4,5]` | ISO weekdays |
| `limit_warnings`, `limit_warning_admins` | `false`, `[]` | empty list = every admin |
| `notify_records` | `false` | |
| `notify_invites` | `true` | a phone notification for a "Play with someone" invite (§13) |
| `ha_sensors` | `false` | |
| `keep_scores_years` | `0` | 0 = forever, or 1, 2, 5 |
| `ai_…` | | AI levels, §11.3 |

Holidays are their own table: `GET/POST /api/admin/holidays`
(`{from, to?, name?}`, at most 120 days at once), `DELETE /api/admin/holidays/{date}`.

## 4. Security and identity

- A middleware refuses every request whose source address isn't the
  Supervisor's ingress proxy (`172.30.32.2`) or loopback
  (`auth.INGRESS_ALLOWED_HOSTS` = `auth_core.INGRESS_HOSTS`, through
  `auth_core.refuse_outsiders`; shared `app/common/auth_core.py`); that is what
  makes the `X-Remote-User-*` headers trustworthy. No user id → 401.
- Admin = the user id or the login name is in `admin_users` (case-insensitive).
  Display names are never matched. Nobody is auto-promoted; while the list is
  empty every page shows the "No admin yet" banner.
- `/api/me` and `/api/whoami` carry the flag `noAdmin` (was `noAdmins`).
  "How the app sees you" (`GET /api/whoami`) is the shared contract
  (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`): only the identity headers, a
  count of admin entries, `noAdmin`, `notifyLinked` and one extra row, Phone
  linked for notifications. The page and the banner are drawn by
  `common/whoami.js`.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data: blob:; connect-src 'self'; …` — no inline scripts, no eval, no outside
  hosts. The headers are added by `web_security.SecurityHeaders.apply` at the end
  of the ingress guard (shared `app/common/web_security.py`). Request bodies over 64 KB are refused (except the import).
- The frontend builds DOM with `textContent` only; all URLs are relative.

## 5. Data (`db.py`)

Migrations are numbered, run once each, in order, and recorded in
`schema_version`. `init_db()` runs them at startup and right after an import.

1. `users` (HA id, display name, login name, disabled, look, sound,
   handedness, reduce_motion, receive_notifications, last_seen, created_at),
   `user_notify`, `play_sessions` (id, person, game, mode, practice,
   started_at, last_beat_at, active_seconds, ended_at, scored), `scores` (id,
   person, game, mode, score, level, seconds, started_at, ended_at,
   app_version, session_id), `app_settings`.
2. `users.is_child`, `child_limits` (minutes school/weekend, quiet hours
   school/weekend from–to, allowed games JSON or NULL = all, leaderboard
   full|first|hidden), `extra_time` (person, date, minutes, given by),
   `holidays` (date, name), `notify_log` (once-a-day notices).
3. `levels`, `level_builds`, `play_sessions.level_count` (§11.4).
4. `levels.builtin_key`, `saved_games`, `play_sessions.base_seconds /
   resumed / first_started_at` (§12).
5. `ai_calls` (§11.10).
6. `matches`, `match_players`, `play_sessions.match_id` (§13.6).

The numbered migrations runner stays in `db.py` (its column check uses
`db_core.columns`). Connections come from `db_core.connect` / `db_core.transaction`.
A backup must contain the migration-1 tables and must not come from a newer
schema (`db_core.validate_file` with the "newer version" check as its extra
hook); it is received (`backup_core.receive`), swapped in and migrated
(`backup_core.restore_file`). The download is `db_core.snapshot_to_tempfile`,
sent by `backup_core.send_file`.

## 6. Play sessions and scores (`routers/play.py`)

- `POST /api/sessions {game, mode, practice}` — 403 for a person switched off;
  409 for a game switched off; 422 for a mode that doesn't exist or is hidden;
  for a child 403 if the game isn't allowed and 409 (with the reason) outside
  their time or during quiet hours. Ends any other open session of the person.
  Returns the session id, a seed, the person's best and the household record
  for that mode, and the play-time picture.
- `POST /api/sessions/{id}/beat {activeSeconds}` — every 15 s while a game
  runs and on pause/resume. Active seconds only grow and are capped at the wall
  time since the session began (+2 s).
- `POST /api/sessions/{id}/end {activeSeconds}` — a game left without a result.
- `POST /api/scores {sessionId, score, level, seconds}` — ends the session
  and, unless it was Practice (`reason: practice`) or under 3 s
  (`reason: short`), stores the score with the app version. Returns
  `{saved, reason, personalBest, householdRecord, best, scoreId, playTime}`.
  A session takes one score.
- Honest scores (422 otherwise): whole numbers, not negative; `seconds` ≤ 6 h
  and ≤ the wall time since the session began; `level` ≤ 100 (with a level
  list, §11.7); score ≤ the game's most and ≤ seconds × its rate + its base
  (`games.py`):

  | Game | Most | Rate a second | Base |
  |---|---|---|---|
  | Snake | 20,000 | 40 | 100 |
  | Brick Breaker | 500,000 | 400 | 500 |
  | Falling Blocks | 75,000,000 | 10,000 | 2,000 |
  | Paddle Duel | 10,000,000 | 3,500 | 2,000 |
  | Lane Racer | 3,000,000 | 150 | 300 |
  | Flap | 100,000 | 2 | 5 |
  | Mines | 2,000,000 | 600 | 1,000 |
  | Merge | 17,000,000 | 800 | 300 |
  | Colour Memory | 2,325,000 | 40 | 50 |
  | Memory Cards | 1,650,000 | 800 | 2,000 |
  | Tap the Mole | 1,000,000 | 40 | 60 |
  | Number Dash | 5,000,000 | 900 | 200 |
  | Tank Battle | 1,500,000 | 160 | 500 |
  | Sky Defenders | 1,300,000 | 150 | 400 |
  | Rocks | 15,000,000 | 700 | 1,000 |
  | Road Hop | 850,000 | 250 | 1,000 |
  | Snake Duel | 5,000,000 | 800 | 500 |

  The rates are what the rules allow at their fastest (a piece every 10
  updates, a point every half second, the top road speed, a gate every
  48 updates), so real play never reaches them; the tests play each game
  with a quick bot and check it stays inside.
- Housekeeping closes sessions without a heartbeat for 2 minutes at their last
  heartbeat.
- `GET /api/scores/mine` — bests per game and mode, last 20, totals (games
  saved, seconds played this week Monday–Sunday, Practice included).
- `GET /api/leaderboard?game=&mode=&period=all|month` — top 10, best per
  person once, people switched off left out; ties go to the earlier score.
  403 if the leaderboard is off or hidden for this child; `first` shows first
  names only.
- `DELETE /api/scores/{id}` — your own, or anyone's for an admin.
- Keep scores for: games older than N years are removed except each person's
  best per game and mode; ended play sessions older than that go too.

## 7. Children (`limits.py`)

- A date is a school day if its weekday is in `school_days` and it isn't a
  holiday; otherwise it uses weekend limits.
- Time used today = the sum of `active_seconds` of the person's sessions that
  started today (Home Assistant's time zone). Allowance = the day's minutes +
  today's extra time; `leftSeconds` is `null` with no limit.
- Quiet hours: a window whose end is before its start runs past midnight. The
  set that applies is chosen by the day the window ends on: ending on a school
  day → school-night times, otherwise weekend times.
- Starting is refused during quiet hours or with no time left; a running game
  always finishes and its score is saved.
- Admins can't be marked as children (409); an admin listed after being marked
  has no limits.
- Admin routes: `PATCH /api/admin/users/{id} {disabled?, isChild?}`,
  `GET/PUT /api/admin/users/{id}/limits`, `POST /api/admin/users/{id}/extra-time
  {minutes: 15|30|60}`, `GET /api/admin/users/{id}/history` (14 days).
- **Admin → Users** is the shared people list (`common/people.js`): one card per
  person with the **Can play** and **Child** switches, a child's limits, extra
  time and play history, and the notify editor (Phones from Home Assistant,
  Also, Add, **Send a test**). `GET /api/admin/users` gives each person's `ha`
  (now also `personName` and `tracker` per phone, from
  `app/common/people_admin.py`); the page also loads
  `GET /api/admin/notify-services` for the Add list.
- `GET /api/me` gives the child's play time and limits; for admins it lists
  children with 5 minutes or less left (`lowTimeChildren`).

## 8. Home Assistant

- Core API calls through the shared `app/common/ha_client.py` (the app's
  `ha_client.py` re-exports it and adds `load_timezone`).
- Time zone from `GET /api/config` at startup (`app/common/ha_time.py`); UTC if it can't be read.
- People and phones: the shared `ha_people.py` (`app/common/`), refreshed every 5 minutes.
- Notifications (`notify.py`, shared `ha_notify.py`): new household records to
  everyone else switched on with `receive_notifications` (not a child whose
  leaderboard is hidden); "5 minutes left" to the chosen admins once per child
  per day, with `url`/`clickAction` and an `actions: [{action: "URI", title:
  "Add 15 minutes", uri: …#/admin/users/<id>}]` when the app knows its panel
  path.
- Sensors (`ha_sensors.py`), only with `ha_sensors` on:
  `sensor.household_arcade_<game>_record`,
  `sensor.household_arcade_<person>_played_today` (minutes; child attributes),
  `binary_sensor.household_arcade_<person>_playing` (a session with a
  heartbeat in the last 90 s). Posted on every change and every 5 minutes;
  turning the switch off posts `unavailable` once. Posting goes through
  `ha_sensors.SENSORS`, a `sensor_publisher.Publisher` (shared
  `app/common/sensor_publisher.py`: change detection on state + attributes, stops
  at the first failure); the 30 s loop is `sensor_publisher.run`, with a
  `Refresh` (300 s, or a date change) for the full re-post.

## 9. Frontend

- `index.html` loads `common/theme-boot.js`, `common/themes.css`,
  `common/settings.css` and `style.css` in `<head>`, then `common/ui.js`,
  `common/settings.js`, `common/people.js`, `common/backnav.js`,
  `common/whoami.js`, the game files in the order of spec/GAMES.md, then
  `app.js`, `play.js`, `admin.js`, `together.js`, all with `?v=<version>`.
- Page themes: Midnight (default, teal accent), Slate, Daylight and Auto
  (Daylight on a light device, else Midnight), from `common/themes.css` and
  applied before first paint by `common/theme-boot.js`; a saved **Ink** becomes
  Midnight. The game looks (Modern, Retro LCD, Neon, Pixel, Paper, High
  contrast) are unchanged and separate.
- Shared helpers come from `common/ui.js` (`h()`, `api` via `UI.makeApi`,
  `toast()`, `openModal()` / `confirmDialog()`; the open-dialog list is
  `UI.dialogs()`, was the global `modalStack`).
- Pages (hash routes, no history entries of their own): `#/home`,
  `#/play/<game>`, `#/scores`, `#/leaderboard`, `#/settings`,
  `#/admin/settings|levels|ai|users|storage`, `#/admin/users/<id>`.
- Games page: three views — **List** (icon, name, best), **Small squares**
  (icon and name) and **Large squares** (icon, name, best, modes, levels and
  the modes that play them, a saved game, games played and when, the game's
  controls text) — picked with ☰ ▦ ◼ and remembered on the device
  (`localStorage`, `arcade.gamesView`; large by default). `/api/games` gives
  `plays`, `lastPlayed`, `levels` and `levelModes` for it.
- The game page (`play.js`) owns the start screen, overlays, controls and the
  session. The start screen is a card (Mode and Look drop-downs, Practice,
  Play) over a still preview of the game in that mode and look (an instance
  that never starts; a new look shows at once); Play makes a fresh game; the game draws only the playfield and HUD on the canvas
  (240 × 300 logical). Keys (arrows, WASD, Space, X, P, Esc) are captured only
  while a game is on screen. A d-pad (≥ 56 px) and swipes for `dpad` games (and a
  tap for games with a `tap` action: Road Hop hops forward);
  dragging on the canvas or the strip below it and a Launch (Serve) button for
  `paddle` games; the game's own buttons (≥ 56 px; one row, or a grid when
  each has a `place` — Falling Blocks: Hold | ◀ ↻ ▶ with ▼ under ↻ | Drop) for
  `buttons` games, and none for `touch` games — both get the canvas pointer
  (taps, drags) themselves. C, F and Shift send `alt` (Hold, Flag). Two-player
  games (`players: 2`, Snake Duel) take the arrows/Space/Enter for player 1
  and W A S D/Q/E for player 2; the controls are centred under the game (beside it in
  landscape), left/right-handed layout; pausing is the ⏸ button at the top.
  Optional Gamepad API (d-pad / left stick, A B = fire, X Y = alt, Start =
  pause; in a two-player game the second controller is player 2; browsers
  offer it on https only).
- **Fitting the screen** (`fitStage` in `play.js`): the stage keeps the game's
  4:5 shape and is set to the biggest size the window allows — the play area's
  width, and the visible viewport's height (`visualViewport`) below the page
  header, less the help line and the page's bottom padding. The controls go
  under the game or beside it (`data-layout="below" | "side"` on the play
  area), whichever leaves the game bigger. It runs again on window and visual
  viewport resizes, orientation changes and size changes of the play area, so
  a folding phone opening or closing, a phone turning or a browser window being
  resized re-fits at once. The game page drops the 1000 px reading width of the
  other pages. The kit keeps a canvas's backing store under about 2.4 million
  pixels (lowering the pixel ratio for very large games) so big screens stay
  smooth. The controls never shrink (the game is sized around their natural
  size, a few pixels short of the full width), so nothing is cut off on wide
  folding phones.
- **How to play** is folded away by default: the **?** button in the game's
  header opens and closes it (remembered per device, `arcade.helpOpen`), and the
  game re-fits to the room it frees. The start card is compact (Mode and Look
  side by side, smaller controls) and drops its title on a short game
  (container query on the stage), so it fits without scrolling.
- The game pauses on `visibilitychange` (hidden), `pagehide`, the pause
  button/P/Esc and the back gesture: a running game is the top "layer" for
  `common/backnav.js`, so Back pauses it first, then leaves the game, then the app.
- The page measures active (unpaused) time itself for the heartbeats and uses
  the game's `result.seconds` for the score.

## 10. Tests

`python -m unittest discover -s tests` (API, auth, first run, admin, children
and limits, scores and leaderboard, sensors with a fake Home Assistant,
storage and migrations, levels and the builder with a fake model, packaging — the shared checks from
`common_tests/packaging_core.py`) and `node --test tests/js` for the game
logic, run from `tests/test_games_js.py` when Node is installed. `tests/common_tests/` (copies) holds the shared
helpers and runs the shared modules' own tests (whoami, auth_core, db_core, settings_core, people_admin,
web_security, backup_core, sensor_publisher, ai_client) and `test_shared_copies.py`.

## 11. Levels and AI-made levels

### 11.1 What it does

- Brick Breaker (both modes share one list) and Snake's **Maze** mode play
  through a **level list**: ten built-in levels each (`level_data.py`, the
  same as `LAYOUTS` / `MAZES` in the game files), then any made by an AI model.
- The model is the household's own (Ollama on the network) or a remote one (an
  OpenAI-compatible service or Anthropic Claude), set up as in Finance
  Dashboard: the requests, retries (429/500/502/503/504/529, Retry-After or
  5/15/45 s), the 400 fall-backs, error mapping and model listing are the
  shared `app/common/ai_client.py`; the app's `ai_client.py` keeps `Config`,
  `current()`, `generate()` and its usage notes (no images).
- **Automatically**: when a game reaches a level within `ai_levels_ahead` of
  the end of the list (reported by `POST /api/scores` and by heartbeats), the
  next `ai_levels_batch` levels are built in the background. Nothing is built
  while nobody is near the end.
- **On request**: Admin → Levels → **Build more levels** (1–20).
- Off until an admin turns it on and sets a model; then the games play their
  built-in lists only (Brick Breaker repeats its list faster; Snake · Maze is
  won after the last maze).
- Snake's Walls and Wrap modes have no list (their "level" is the speed).
- Every other game has a list too, described in `level_kinds/<game>.py`
  (§11.9); `games.py` names the modes that play it (`level_modes`) and
  whether finishing the list ends the game, won (`levels_end`, then a result
  can claim at most as many levels as the game was given).

### 11.2 The rule: the model writes data, never code

- A level is a small JSON object (a brick grid, a maze of walls); the CSP stays
  `script-src 'self'`, so a level can't run anything.
- The server checks every level the model returns (`levels.validate`, §11.5)
  before it is stored; the game files check the shape again and skip a level
  that doesn't fit, falling back to their built-in list.
- Only the server talks to the model; the browser never sees the access key.
  The prompt holds the game's rules, the format, a target difficulty and the
  most recent levels (so new ones differ) — nothing about the people here.

### 11.3 App settings (group *AI levels*)

| Key | Default | |
|---|---|---|
| `ai_levels_enabled` | `false` | the switch |
| `ai_provider` | `ollama` | `ollama`, `openai` (OpenAI-compatible), `anthropic` |
| `ai_url` | `""` | needed for Ollama; empty = the provider's usual address for the others |
| `ai_model` | `""` | needed |
| `ai_api_key` | `""` | write-only (the page gets `secretsSet.ai_api_key`); needed for Anthropic; `""` clears it |
| `ai_max_output_tokens` | `4000` | 256–64000 |
| `ai_levels_auto` | `true` | build ahead automatically |
| `ai_levels_ahead` | `2` | 1–5 |
| `ai_levels_batch` | `5` | 1–20 |
| `show_daily_challenges` | `false` | the daily challenge (below); off = hidden everywhere and daily scores refused (404) |
| `sudoku_hints` | `3` | hints per Sudoku puzzle, 0–20 (Practice: unlimited) |
| `ai_levels_daily_limit` | `20` | levels made a day (Home Assistant's day, all lists); 0 = no limit |
| `ai_levels_review` | `false` | new levels wait (`waiting`) for an admin's OK |
| `ai_price_in` | `0` | price per million input tokens, only for the estimate on AI usage (0–1000) |
| `ai_price_out` | `0` | price per million output tokens, the same |

`settings.ai_problem()` says why levels can't be built (off, no model, no
address, no key). `POST /api/admin/ai/test` (Test connection) uses the page's
unsaved values (an empty key = the saved one): it lists the provider's models
and asks the model one tiny question.

### 11.4 Data (migration 3)

- `levels`: id, game (the list: `brick`, `snake`), number (1, 2, 3 …, never
  reused), name, data (JSON), `source` (`builtin` | `ai`), `status` (`ready` |
  `waiting` | `retired`), difficulty (0–100), fingerprint (the same for a layout
  and its mirror image), model, build_id, created_at, approved_by. The built-in
  levels are written by `init_db()` at startup and after an import.
- `level_builds`: id, game, requested_by (a name, or `auto`), count, `status`
  (`queued` | `running` | `done` | `failed`), made, rejected, requests, tokens
  in/out, model, error, log (short notes), created/started/ended. Builds left
  queued or running when the app stops are marked failed at the next start.
- `play_sessions.level_count`: how many levels the game was given.

### 11.5 Checks (`levels.validate`)

- **Shape**: exactly the list's fields; anything else is refused.
- **Brick Breaker** `{name, rows}`: 8 columns, 3–10 rows after dropping empty
  rows at the bottom, only `.` `1` `2` `3`, at least 12 bricks, 30–150 hits in
  all. Difficulty = hits as a share of 150.
- **Snake · Maze** `{name, walls, foods}`: 20 × 20 of `.` and `#`; row 10,
  columns 6–12 free (the snake's start and the cells ahead); at least 60 % free;
  every free cell reachable from the start; foods 5–30.
- **Name**: 1–40 characters, letters, digits, spaces and simple punctuation,
  none of a short list of words (children see the names).
- **No repeats**: a level whose fingerprint is already in the list (or whose
  mirror image is) is refused.

### 11.6 Building (`level_builder.py`)

- One build at a time per list; builds run one after another in a worker
  thread (`run_pending()` in tests).
- A build asks for at most 5 levels a request, checks each, stores the good
  ones (status `ready`, or `waiting` with review on) and notes why the others
  were turned down. It ends when it has made what was asked, when the daily
  limit is reached, on a provider error, or after 3 requests in a row that
  added nothing. Made anything → `done`, else `failed`.
- `POST /api/admin/levels/build` → 404 unknown list, 422 a count outside 1–20,
  409 not set up / already building / the list is full (500), 429 the daily
  limit is used up. The count is cut to what is left today.

### 11.7 Play

- `POST /api/sessions` returns `levels` (the playable list, `null` for modes
  without one) and `levelsBuilding`; the game is given them as `opts.levels`
  and keeps them to the end. The level limit for a result is the list's length
  for Snake · Maze and five rounds of it (at least 100) for Brick Breaker.
- Heartbeats may carry `level`; `POST /api/scores` returns `levelsComing`
  (a build for that list is waiting or running), shown as "New levels are on
  the way".
- Brick Breaker: within one pass through the list the ball starts 4 % faster
  each level for ten levels, then 1 % a level; each repeat of the list is 15 %
  faster (never above the top speed).
- Snake · Maze: Normal speed plus 0.3 cells a second per maze; eating the
  maze's foods scores 50 × its number and starts the next maze with the snake
  back at the start; clearing the last maze wins.

### 11.8 Admin → Levels and API

- `GET /api/levels?game=&mode=` — the playable list (anyone signed in).
- `GET /api/admin/levels` — per list: every level (with its data, for the
  small pictures), counts, the furthest anyone got (saved scores), the last
  build; plus the AI set-up, levels made today and the daily limit.
- `GET /api/admin/levels/{id}`, `GET /api/admin/levels/builds/{id}`.
- `POST /api/admin/levels/{id}/retire | restore | approve` — the last playable
  level of a list can't be retired (409).
- `POST /api/admin/levels/{id}/move {to}` — put the level at place `to`
  (1-based, 422 outside the list); the list is renumbered 1…n. Built-in levels
  are known by `builtin_key` (`brick:1` …), so they keep the place given to
  them across restarts; a new built-in level is added at the end.
- `DELETE /api/admin/levels/{id}` — levels the AI made only (409 for a
  built-in level or the last playable one); the list closes up.
- The page polls while a build waits or runs. Each list's level pictures are
  folded away (`<details>`, closed at first; drawn when opened), so the page
  stays short however many games and levels there are.

### 11.9 Level lists for every game (`level_kinds/`)

Brick Breaker and Snake · Maze keep their own checks and prompts
(`levels.py`, `level_builder.py`). Every other game describes its list in
`app/level_kinds/<game>.py` as a `KIND`: the label and noun shown to admins
and the model, the modes that play it, each field with its check (`Int`,
`Num`, `Bool`, `Choice`, `Text`, `Grid`, `List` — ranges chosen so that no
level the model could write breaks the game's honest-score limits or can't be
played), a cross-field `check` (reachability, counts that fit, steps a player
can manage), `difficulty`, the text that explains the game to the model, a
per-level target and an example, and the built-in levels. `level_kinds`
checks every level the same way and writes the prompt. The rules file has the
same built-in list (`LEVELS`, compared by the tests) and checks the shape
again (`usableLevels`).

| Game | List | Modes that play it | A level |
|---|---|---|---|
| Falling Blocks | Challenges | `challenge` | starting rows, rows to clear, speed |
| Paddle Duel | Opponents | all | paddle speed, miss chance, aim, serve speed, points to win |
| Lane Racer | Stages | `stages` | lanes, speed, rows to the finish, traffic, trucks, coins |
| Flap | Courses | `course` | gates, gap, speed, sway, spacing, a pattern of heights |
| Mines | Shaped boards | `boards` | a shape of squares, mines |
| Merge | Goals | `goals` | board size, the tile to make, fixed stones |
| Colour Memory | Challenges | `challenge` | length, light and gap times, backwards, starting length |
| Memory Cards | Challenges | `challenge` | columns, rows, time, peek, kinds of symbol |
| Tap the Mole | Gardens | `gardens` | a shape of holes, bonks to clear, how long and how often, hedgehogs, golden moles |
| Number Dash | Challenges | `challenge` | which sums, how big, right answers to clear, seconds |
| Tank Battle | Arenas | all | a 13 × 13 map, enemies, their speed and fire |
| Sky Defenders | Waves | `waves` | the formation, speed, bombs, shields |
| Rocks | Waves | `waves` | big and medium rocks, speed, saucer chance |
| Road Hop | Levels | `levels` | the lanes (road, river, grass; pattern, speed, direction), time |
| Snake Duel | Arenas | all | walls, speed, foods on the board |

In every list mode, finishing a level starts the next one and finishing the
last ends the game won (`levels_end`). Points never grow with the level
number beyond a cap, so long lists keep the per-second limits true; the
highest scores a 500-level list could reach are within each game's most.

### 11.10 AI usage (`ai_usage.py`, Admin → AI usage)

- Every request to the model is a row in `ai_calls` (migration 5): time,
  purpose (`build` | `test`), game and build for builds, provider, model,
  ok / error, tokens in and out, milliseconds, levels made and turned down by
  that answer. Writing it never stops a build. Rows older than 400 days are
  removed by housekeeping.
- `GET /api/admin/ai/usage?days=7|30|90` (admins): totals for today, 7 days,
  30 days and all time (requests, failed, tokens in/out, levels made and
  turned down, and an estimated cost when `ai_price_in` / `ai_price_out` are
  set), tokens by day, by game and by model for the period, and the latest 25
  requests.
- The page: four tiles, a bar chart of tokens a day (7 / 30 / 90 days), tables
  by game and by model, and the latest requests.

## 12. Saved games and ending early

- **Ending early**: End game (pause screen) and leaving the game page mid-game
  (Back, another page, `pagehide` with `keepalive`) send the score so far to
  `POST /api/scores` like a finished game (`stats.quit`); the usual checks
  apply. Nothing is sent for a game with no score under 3 seconds.
- **Saving** (`saves.py`, migration 4 `saved_games`, at most one per person
  and game): `POST /api/sessions/{id}/save {state, stateVersion, score, level,
  seconds}` — the game's rules state as plain data (`Logic.save`, ≤ 48 KB),
  its format (`games.py` `state_version`, the game file's `STATE_VERSION`;
  another one is refused) and the result so far, checked like a score (time
  ≤ time played). The level list is kept with the save. The session ends
  without a score. A save of the same game from another session is replaced;
  the replaced one is ended with its score (`finalize`).
- **Continuing**: `POST /api/sessions {game, resume: true}` — the save's mode
  and Practice; children's limits apply; 409 if its format is out of date.
  The session carries `base_seconds` (time already played),
  `first_started_at` and `resumed`; the response has `saved {state, score,
  level, seconds}` and the save's `levels`. The game is created with
  `opts.restore = {state, seconds}` and carries on (its seconds continue). A
  result may claim at most `base_seconds` + this session's time; only this
  session's active time counts for limits. When the continued game ends with a
  score, the save is used up.
- `GET /api/saved`; `/api/games` has `saved` and `canSave` per game.
  `DELETE /api/saved/{game}?keepScore=true|false` — End it (score kept, unless
  Practice, under 3 s or not possible) or Throw away.
- Start screen: the saved game (mode, score, level, time) with **Continue**,
  **End it**, **Throw away**; **New game** plays a new one. Pause screen:
  **Resume**, **Save for later** (asks first when it would replace a saved
  game), **End game**.

## 13. Playing together from two phones (step 1, Race, is built; steps 2 and 3 are the plan)

Two people in the household, each signed in to Home Assistant on their own
phone (or computer), play one game together. It comes in three steps, each a
release of its own. **Step 1 (Race, §13.3) is built**: invites, the match tables
(migration 6), the live link with its long-poll fallback, the race screen,
Rematch, head to head and the App setting *Invites by phone notification*; the
sections below say "Built" where they describe what exists and keep the plan
for live duels (§13.4) and turn by turn (§13.5), which don't exist yet.

### 13.1 What it does

*Built for races (§13.3); "a child out of time ends the game for both, as a draw"
is for live duels: in a race a running game always finishes, as everywhere
(§7), and each child's own limits decide whether they can start or join.*

- **Invite.** On a game's start screen, **Play with someone** lists the people
  in the household who may play that game now (people switched off, children
  outside their time or quiet hours, and children not allowed that game are
  left out, with the reason). Picking one sends them an invite.
- **The invite** reaches them three ways: a Home Assistant phone notification
  ("Asha challenges you to Snake Duel", with **Join** and **Not now** buttons;
  Join opens the app on that game), a banner on the app's Games page, and a
  "Waiting for you" list there. An invite lasts 5 minutes for live games
  (§13.3, §13.4) and 7 days for turn-by-turn games (§13.5); the one who sent it
  can cancel it. A person has at most one live invite out at a time.
- **Both play signed in.** Each person's score, the result (won, lost, draw)
  and the play time are saved for them, as for any game: personal bests,
  leaderboard, Practice (both must agree), children's daily minutes (each
  player's own clock; a child out of time ends the game for both, as a
  draw), quiet hours.
- **Head to head**: My scores gains "Against others" — for each person you've
  played: games, wins, losses, draws, per game. The leaderboard is unchanged
  (still single scores).
- **Who it's for**: the people of this Home Assistant only. Nothing goes
  through any outside service; away from home it works through the same
  Home Assistant connection the app already uses (e.g. Home Assistant Cloud).

### 13.2 How the phones talk

**Built (races).** `app/together.py` (the match logic), `app/routers/together.py`
(routes and the socket), `static/together.js` (the browser side). One live
link per player, chosen automatically in the browser:

- **WebSocket** `GET /api/matches/{id}/live` through ingress (relative URL,
  `ws`/`wss` to match the page). Uvicorn's WebSocket library is `wsproto`
  (`requirements.txt`, pure Python). Starlette's HTTP middleware doesn't see
  WebSockets, so the route itself refuses a source address that isn't
  the ingress proxy or loopback (`auth.INGRESS_ALLOWED_HOSTS`), an `Origin`
  whose host isn't the request's `Host`, no identity headers, and a person
  who isn't in the match. The CSP names the app's own host for `ws:`/`wss:`
  next to `'self'` (`main._csp_for`) for browsers that don't count `'self'`
  as a WebSocket source. The phone sends `{"t":"state", score, level, over,
  paused}` about every 0.3 s while it changed and at least every 2 s; the
  server pushes `{"t":"match", ...}` whenever anything changes and at least
  every 3 s; a phone silent for 30 s is dropped.
- **Long poll** when the socket can't open (3 s), errors or drops:
  `POST /api/matches/{id}/state` for the phone's numbers and
  `GET /api/matches/{id}?since=<v>&wait=20` held open until the match's `v`
  changes. Same picture, same messages' content.
- The live numbers are display only: they live in memory (`together.LIVE`),
  are clamped (score ≤ the game's most, level ≥ 1) and are never a score. A
  phone is "connected" when it was heard from in the last 6 s.
- Pause is local: either phone's pause shows "paused" to the other, nobody's
  game waits (no lockstep is needed for a race).
- Not measured yet: whether the WebSocket survives ingress on the companion
  apps and Home Assistant Cloud; the fallback is what makes that safe to
  ship. The test release the plan asks for before step 2 is still to do.

*The rest of this section is the plan for live duels (step 2).*

- One **WebSocket** per player to the app (`GET /api/match/{id}/live`, through
  Home Assistant's ingress like every other request; same identity headers,
  same ingress-only check). Uvicorn needs a WebSocket library: `wsproto`
  (pure Python) is added to `requirements.txt`. Before step 2 is built, a
  small test release checks that the link stays up through ingress on the
  companion apps (Android, iOS) at home and through Home Assistant Cloud, and
  measures the delay; if it doesn't hold, live duels fall back to long-polling
  (`POST /api/match/{id}/inputs` every update batch).
- **The server is a relay and referee, not a game engine.** It never runs the
  games (they are JavaScript in the browser). It passes inputs between the two
  phones, keeps the match's record, checks the results both phones report
  (they must agree) and applies the usual honest-score limits to each.
- **Lockstep for live duels.** The games already run in fixed updates (60 a
  second) from a seed, so both phones run the *same* game: the server sends
  both the seed, the mode and the level list; each phone sends only its own
  inputs, stamped with the update they apply to; an input is played
  `delay` updates after it was made (3 at home, chosen from the measured
  round trip, up to 12 — 0.2 s — away from home), so both phones apply every
  input at the same update and the games stay identical. A phone that hasn't
  got the other's inputs for an update waits (the game freezes briefly, with
  "Waiting for Kabir…"); after 10 s of silence the match is paused, after 60 s
  it ends (§13.6). Every second each phone sends a short checksum of its game
  state; if they ever differ the match ends as "out of step" with no result
  (and a log line), which the tests make sure never happens.
- **Pausing**: either player's pause (or their phone hiding the app) pauses
  both. Saving a live match for later is not offered.

### 13.3 Step 1 — Race (built)

**As built.** A race is a `matches` row (kind `race`) with two `match_players`.
The inviter chooses mode and Practice on the start screen; the match fixes the
seed and, for a level-list mode, the level list (both phones get them from
`POST /api/sessions {game, matchId}`, which also takes the match's mode and
Practice and refuses a second session for the same person). Each player's
session is an ordinary play session (`play_sessions.match_id`), so limits,
quiet hours, heartbeats and play time work unchanged; the player's final
score is the one they send to `POST /api/scores` (so it is checked by the
honest-score limits and saved as a normal game, except Practice), and the
server copies it into `match_players` (an impossible score counts as 0 and
loses). The shell adds `won` to that request in a race so puzzle rules
can see who solved it. Winners (`together.decide`): by the game's race rule in
`games.py` (`"race": {"rule": "score"}` default, higher score, tie a draw, or
with `"tiebreak": "faster"` the shorter game; `{"rule": "fastest"}`: solved
beats not solved, both solved the shorter game, neither the higher score;
`False` = no race); with no `race` key a game is raced only if it is in
`together.RACE_DEFAULT` (the list below, so new games opt in).
A player who left (session ended or stale for 2 minutes, or never started in
2 minutes) loses to the one who finished; both gone: no winner; 7 hours
without a result ends it. Raced: Snake, Brick Breaker, Falling Blocks,
Lane Racer, Flap, Mines, Merge, Colour Memory, Memory Cards, Tap the Mole,
Number Dash, Sky Defenders, Rocks, Road Hop. Not raced: Paddle Duel and Tank
Battle (left out of the plan's list), Snake Duel (two players on one screen).
The shell's hooks: the game definition's `race` flag (default true unless
`players: 2`), the kit session's `status()` (score, level, over, paused;
nothing changes in any game file), the game page's race bar, count-in and
result card (`play.js`), and a `GET /api/games` field `race` that is the
server's decision. Every raced game is checked in Node: two phones with the
same seed and the same inputs end in an identical state.

The plan, as before:

The same single-player game on both phones, with the same seed (the same
traffic, the same pieces, the same sums), side by side: each plays their own
game, and a bar at the top shows the other's score, level and whether they are
still playing, updated a few times a second. It doesn't need lockstep, so
delay doesn't matter.

- Games: Lane Racer, Flap, Falling Blocks, Merge, Number Dash, Colour Memory,
  Memory Cards, Tap the Mole, Mines, Rocks, Sky Defenders, Road Hop (every
  single-player game whose randomness comes from the seed; Brick Breaker and
  Snake too).
- Later games get a race as they're built (each game's seed gives both players
  the same start; the winner is decided as listed):
  - **Sudoku** — the same puzzle; the faster finish wins, with the usual
    penalties (30 s per hint, 10 s per shown mistake). Hints and notes are each
    player's own.
  - **Word Guess** — the same word; fewer guesses wins, then the faster time.
  - **Word Search** — the same grid; all words found first wins (or more words
    when one gives up).
  - **Slide Puzzle**, **Lights Out**, **Picture Logic**, **Tile Match** — the
    same board; solved first wins (Slide Puzzle and Lights Out: fewer moves
    breaks a tie).
  - **Code Breaker** — the same hidden colours; fewer rows wins, then the
    faster time.
  - **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander**,
    **City Defense** — the same level and the same sequence; the higher score
    wins (Lander: landed with more fuel left).
  - **Type Rain** — the same words in the same order; higher score wins, words
    per minute breaks a tie.
- A race where one player finishes a puzzle while the other is still playing
  shows "Asha finished in 6:12" on the other phone; they can keep going to post
  their own time or give up.
- Modes: any mode both pick together (the inviter's choice), including the
  level-list modes; both get the same level list.
- The winner: the higher score when both games have ended; a game that ends
  early just waits, watching the other's score. Each person's score is also
  saved as a normal game in that mode.
- Shell: a `race` capability on the game definition (all of the above; no
  change in the games' rules files beyond reporting a small `status()` —
  score, level, over).

### 13.4 Step 2 — Live duels

- **Snake Duel** first (both modes become: against the computer, two players on
  one screen, **two phones**), then **Paddle Duel** (a new "two phones" mode:
  each player sees their own paddle at the bottom — the second phone draws the
  court upside down), then **Tank Battle** (two tanks: *together* — protect one
  flag — or *against each other* in an arena with a flag each).
- **Carrom** (when it's built): two players, live, taking turns — each shot is
  the striker's position, aim and power, sent to both phones, and both phones
  play the same shot with the same physics. The physics use whole-number
  (fixed-point) maths, not floating point, so every phone gets exactly the
  same result; a checksum after each shot confirms it (out of step ends the
  match with no result). A player has 30 s to take a shot (then a weak shot is
  played for them). Doubles (four players, two teams) are possible later.
- A duel game registers `players: 2` and `lockstep: true`; its rules take
  inputs per player (`press(s, action, down, player)`), are already
  deterministic, and gain `checksum(s)`.
- Score: each player's own (Snake Duel: food, rounds and matches won for that
  player; Paddle Duel: as now from each side). Honest-score limits as now,
  checked for each player.

### 13.5 Step 3 — Turn by turn

Wave 7's family games (Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and
Boxes, Sea Battle) are played this way from the start, and Chess from wave 8:
and so are **Ludo** and **Snakes and Ladders** from wave 8, for **2 to 4
players** (the inviter picks up to three others; the match starts when all
have joined or the inviter starts with those who have; anyone who leaves is
played by the computer). In these two the **dice are rolled by the server**
(the move request asks for a roll, the server answers with it and stores it),
so nobody can choose their roll. Each move is sent to the server, **checked there** (each game has a small Python
rules module that knows the legal moves — the only games whose rules run on the
server), stored, and the other player gets a notification "Your move in Four in
a Row against Asha" (at most one every 15 minutes per match, none in quiet
hours). A match can last days; both players see it in "Your games" on the
Games page. Either can resign; 7 days without a move ends it as a loss for the
one who didn't move.

### 13.6 Data (migration 6; `match_moves` is for step 3)

**Built (migration 6):** `matches` and `match_players` as below (the live
picture's version number `v` is in memory, not in the table), plus
`match_players.won` (the game was won, for the fastest rule), `matches.level_count`,
`matches.expires_at`, `matches.rematch_of` and `play_sessions.match_id`.
`matches.practice` is what the inviter proposed; accepting is agreeing, and the
other person can't turn a ranked race into Practice. Housekeeping (every 10
minutes and whenever a match is read) expires invites after 5 minutes, ends
matches left behind, forgets live numbers 10 minutes after the end; Keep scores
for removes finished matches older than the limit.

- `matches`: id, game, mode, kind (`race` | `live` | `turns`), seed, level list
  (JSON, as for sessions), practice, status (`invited` | `playing` | `paused`
  | `done` | `declined` | `expired` | `cancelled`), created_by, created_at,
  started_at, ended_at, end reason (`finished`, `left`, `timeout`,
  `out_of_step`, `resigned`, `time_limit`), winner (user id, NULL for a draw).
- `match_players`: match, user, seat (1–2; 1–4 for Ludo and Snakes and
  Ladders), invite status, played-by-computer flag, session id (the
  usual play session, so limits and play time work unchanged), score, level,
  seconds, result.
- `match_moves` (turn-by-turn only): match, number, seat, move (JSON), dice roll
  (Ludo, Snakes and Ladders), at. Carrom's shots are stored here too (a live
  match taking turns), so a dropped connection can catch up.
- Live inputs aren't stored (only kept in memory while the match runs).
- Housekeeping: invites expire; live matches with nobody connected for 60 s end;
  finished matches are kept like scores (Keep scores for).

### 13.7 API

**Built (races):** `GET /api/players?game=` → `{players: [{id, name, canPlay,
reason, active}]}` (409 for a game that can't be raced); `POST /api/matches
{game, mode, opponents: [id], practice?, kind: "race", rematchOf?}` → 201 (a
person with an invite out, or in a match, gets 409; so does a person who can't
play now, with the reason); `GET /api/matches` →
`{waiting, sent, playing, recent}`; `GET /api/matches/{id}` (with `since`
and `wait`, a long poll); `POST /api/matches/{id}/state | accept | decline |
cancel`; `GET /api/matches/{id}/live` (WebSocket); `GET /api/against`; and
the session/score routes above. There is no separate `result` route: a
race's result is the player's own score. The notification
(`together.invite_blocking`, a background task) is "Asha challenges you to
Lane Racer" (or "wants a rematch in") with `actions` Join and Not now as URI
actions to `#/home/join/<id>` and `#/home/decline/<id>` when the app knows its
panel path; behind the App setting `notify_invites` (default on, label
*Invites by phone notification*, group Home Assistant) and the person's
*Receive notifications*.

- `GET /api/players?game=` — who can be invited now (with reasons for the rest).
- `POST /api/matches {game, mode, kind, opponents, practice}` → 201 (invites
  sent; `opponents` is one person, or up to three for Ludo and Snakes and
  Ladders); `GET /api/matches` (mine: waiting for me, sent, playing, recent);
  `POST /api/matches/{id}/accept | decline | cancel | resign`.
- `GET /api/matches/{id}/live` (WebSocket) — messages: `hello` (seat, seed,
  mode, levels, delay), `ready`, `start`, `input` {update, actions}, `state`
  {score, level, over} (race), `checksum` {update, value}, `pause` / `resume`,
  `peer` (connected, waiting, gone), `end` {reason, results}.
- `POST /api/matches/{id}/result {score, level, seconds}` — each player's own;
  stored when both have reported (or the other has gone), checked against the
  honest limits; live duels' results must match each other.
- `POST /api/matches/{id}/move {move}` (turn by turn) — 409 if it isn't your
  turn, 422 if the move isn't legal. `POST /api/matches/{id}/roll` (Ludo,
  Snakes and Ladders) — the server's dice roll for the player whose turn it
  is.
- Notifications use the existing notify settings (each person's linked phone,
  the "Receive notifications" preference); a new App setting **Invites by phone
  notification** (on) can turn them off household-wide.

### 13.8 Controls and screens

**Built (races):** the start card's **Play with someone** (next to Play) opens
a sheet with the people; then a waiting overlay (invite time left, Cancel), the
3-2-1 count-in on both phones (from the server's relative `startsInMs`, so the
two clocks needn't agree), the race bar (the other's first name, score, level,
a connection dot and "playing / paused / finished in 6:12 / connection lost? /
getting ready") above the game, and a result card inside the game's frame with both
results (plus, for the puzzles, the game's own summary lines from `result.stats.summary` and a **Not solved —
scores 0** badge when the server answers `reason: "unfinished"`), **Rematch** (the other's phone shows **Join <name>'s rematch**) and
**Back**. An invite also pops up as a sheet wherever the app is open (not during
a game), and shows on the Games page (**Waiting for you**, my own invite with
Cancel, a race that is starting). The page re-fits with the bar (`fitStage`
measures from the play area down). Pausing a race has no Save for later and
the end button reads "Give up (score kept)".

- Each player uses their own phone's usual controls (buttons, swipes, an Xbox
  or other controller). The two-on-one-screen layouts stay for playing on one
  phone.
- Start screen: **Play with someone** next to Play; a waiting card ("Waiting
  for Kabir to join…", Cancel) until they join; then a 3-2-1 count-in on both
  phones.
- During play: the other player's name, score and connection dot at the top;
  "Waiting for Kabir…" when their inputs are late.
- After: both results side by side, **Rematch** (a new invite the other can
  accept with one tap) and **Back**.

### 13.9 Tests

**Built (races):** `tests/test_together.py` (players and reasons, invites, the
one-at-a-time rule, expiry, accept/decline/cancel and who may, notifications
with the fake Home Assistant, the match's sessions and results, winners and
ties, left and stale players, long poll, WebSocket relay with two clients and
the refusals, head to head, housekeeping, the migration) and
`tests/js/race.test.js` (identical games from the same seed for every raced
game, `status()`, the registry flag, the browser link's WebSocket / fallback /
drop handling, the race bar and headline). Packaging: `wsproto` in
requirements; no outside service is contacted.

- Lockstep: two game instances fed the same inputs with random delays and
  reordering through a fake relay stay identical (checksums) for every duel
  game; a dropped peer pauses then ends the match.
- Server: invites (who can be invited, children's limits, one live invite at a
  time, expiry), accept/decline/cancel, results (both agree, honest limits,
  practice), head-to-head totals, turn-by-turn move checking and turn order,
  notifications (fake Home Assistant), the WebSocket relay with two test
  clients.
- Later games, as each is built: its race winner rule (time, guesses, rows,
  moves, score) with ties; Carrom's fixed-point physics gives identical results
  for the same shot on two instances (checksums over many random shots); Ludo
  and Snakes and Ladders with 2, 3 and 4 players, server dice that a player
  can't choose or repeat, and a player who leaves taken over by the computer.
- Packaging: `wsproto` in requirements; no outside service is contacted.

## Planned games

Every game is a module in `static/games/` behind the same start screen, pause,
scores and limits; adding one means adding its files, one registry entry, one
entry in `games.py`, its level list (`level_kinds/<game>.py`, §11.9) and a line
in this file. Waves 1–4 are done (Sudoku, Word Guess, Word Search and the daily challenge — Solitaire was in wave 4 and has been removed; Falling Blocks, Paddle Duel, Lane Racer,
Flap; Mines, Merge, Colour Memory, Memory Cards, Tap the Mole, Number Dash;
Tank Battle, Sky Defenders, Rocks, Road Hop, Snake Duel); the rest will be
added in this order, after playing together (§13). Every one of them can be
played together from two phones from the start: a race (§13.3) for waves 4–6,
turn by turn (§13.5) for wave 7, Chess, Ludo and Snakes and Ladders, and a live
match taking turns (§13.4) for Carrom:

4. **Sudoku** (Easy to Expert, notes, number lines, hints), **Word Guess**,
   **Word Search**, and the **daily challenge** (below).
5. **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander**,
   **City Defense**.
6. **Slide Puzzle**, **Lights Out**, **Picture Logic**, **Tile Match**,
   **Code Breaker**, **Type Rain**.
7. Turn-by-turn family games: **Four in a Row**, **Tic-tac-toe**, **Checkers**,
   **Reversi**, **Dots and Boxes**, **Sea Battle** (same screen, or from two
   phones turn by turn as in §13.5).
8. **Ludo** and **Snakes and Ladders** (2–4 players, on one phone or each on
   their own), **Carrom** (on one phone, or live from two phones), **Chess**
   (against the computer, or turn by turn).

### Daily challenge (built with wave 4)

Built: `app/daily.py`, `routers/daily.py`; the pool is Snake, Falling Blocks, Mines, Merge, Sudoku and Word Guess, three a day. Wave 4 games have no level lists (puzzles come from the seed), higher score is better for each, and Sudoku and Word Guess keep nothing at score 0.

- **Off unless an admin turns it on**: App setting **Show daily challenges**
  (group *Games*), off by default. While it is off nothing about daily
  challenges appears anywhere — no Home card, no start-screen entry, no
  leaderboard tab, no sensors or notifications — and the server refuses daily
  scores (404 "Daily challenges are turned off"). Turning it off keeps the
  scores already saved; turning it back on shows them again.
- When on: each day (Home Assistant's time zone) the app picks the same seeded
  game for everyone — one each of a few games that suit it (Snake, Falling
  Blocks, Mines, Merge, Sudoku, Word Guess, as each one exists). A **Today's
  challenges** card on Home shows them and whether you've played; each person
  gets one ranked try per challenge (Practice replays aren't saved), and each
  challenge has its own leaderboard for the day plus a monthly "days played"
  count. Children's limits, quiet hours and allowed games apply as for any
  game; a game an admin has switched off isn't picked.
- Data: `daily_challenges` (date, game, mode, seed); daily scores are ordinary
  `scores` rows with mode `daily-<date>`.
- Tests: hidden and refused while the setting is off; the same seed for everyone
  on a day; one ranked try; switched-off games never picked; keeping scores when
  turned off and on again.

Not planned: a full-screen TV leaderboard, a "game of the day".

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass. The live WebSocket's origin check is `web_security.cross_origin_websocket`.
- **Backups without secrets**: the download blanks the secret App settings (`ai_api_key`) in the copy (`settings.REGISTRY.scrub_secrets`); a restore keeps this install's value for each one the file leaves blank (`saved_secrets` before, `keep_secrets` after the migrations; a value the file carries is used).
