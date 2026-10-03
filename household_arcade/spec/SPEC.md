# Household Arcade — how it works

Classic arcade games for everyone in a Home Assistant home, with personal
bests, a household leaderboard and play-time limits for children. This file
describes the app as it is now; the next build (playing together from two
phones, §13) and the games still to come are at the end.

## 1. Scope

- Seventeen games, written from scratch (no ROMs, no emulators, no commercial
  names, artwork, music or level layouts): **Snake**, **Brick Breaker**,
  **Falling Blocks**, **Paddle Duel**, **Lane Racer**, **Flap**, **Mines**,
  **Merge**, **Colour Memory**, **Memory Cards**, **Tap the Mole**, **Number
  Dash**, **Tank Battle**, **Sky Defenders**, **Rocks**, **Road Hop** and
  **Snake Duel** (two players on one screen, or against the computer).
- Every game has a level list that an AI model can add to (§11); the modes
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
  main.py         lifespan (migrate, time zone, people, loops), ingress source check, CSP, routers
  config.py       admin_users, DATA_DIR, version, Home Assistant time zone (UTC fallback), now()/today()
  db.py           schema as numbered migrations, schema_version, backup/import
  auth.py         identity from X-Remote-User-*, require_admin
  settings.py     App settings (app_settings table, cached, applied without restart)
  levels.py       level lists: built-in levels (level_data.py), checks, the levels table
  level_builder.py  building more levels with the AI model (ai_client.py)
  games.py        the server's game table: ids, names, modes, score limits; the looks
  limits.py       children: day type, quiet hours, time used/left, limits validation
  scores.py       saving, personal bests, records, leaderboard, retention
  notify.py       record notifications, limit warnings to parents
  ha_sensors.py   the optional sensors
  housekeeping.py stale sessions, Keep scores for
  ha_client.py    Supervisor Core API (stdlib urllib)
  ha_notify.py, ha_people.py   shared with the other household apps (identical copies)
  routers/        me.py, prefs.py, play.py, users.py, admin.py, levels.py
  static/
    index.html theme-boot.js style.css backnav.js (shared) app.js play.js admin.js
    games/        the games (see spec/GAMES.md): kit.js sound.js registry.js, then <game>-logic.js
                  (rules) and <game>.js (drawing) for each of the 17 games
  level_kinds/    one file per game (but Brick Breaker and Snake · Maze): its level format and checks (§11.9)
  level_common.py the level name check and LevelError, shared by levels.py and level_kinds
  ai_usage.py     every request to the AI model, and the AI usage report (§11.10)
tests/            unittest suite (python -m unittest discover -s tests); tests/js for the games (node --test)
```

## 3. Manifest and options

`slug: household_arcade`, `ingress: true`, `ingress_port: 8104`,
`panel_icon: mdi:gamepad-variant`, `panel_title: Arcade`, `panel_admin: false`,
no `ports:`, `homeassistant_api: true` (time zone, people, notifications,
sensors), every other API flag false, no `map:`. Architectures amd64, aarch64,
armv7, armhf, i386. The only option is `admin_users` (empty by default).
Database `/data/arcade.db`.

### 3.1 App settings (`settings.py`, Admin → App settings)

`GET/PUT /api/admin/settings` → `{values, defaults, meta, games, looks, admins}`.
Unknown keys or bad values are a 422 and nothing is saved. Every key applies
at once.

| Key | Default | |
|---|---|---|
| `disabled_games` | `[]` | game ids switched off (new games start on) |
| `default_look` | `modern` | |
| `brick_powerups` | `true` | off hides the `powerups` mode and refuses it |
| `leaderboard` | `true` | |
| `school_days` | `[1,2,3,4,5]` | ISO weekdays |
| `limit_warnings`, `limit_warning_admins` | `false`, `[]` | empty list = every admin |
| `notify_records` | `false` | |
| `ha_sensors` | `false` | |
| `keep_scores_years` | `0` | 0 = forever, or 1, 2, 5 |
| `ai_…` | | AI levels, §11.3 |

Holidays are their own table: `GET/POST /api/admin/holidays`
(`{from, to?, name?}`, at most 120 days at once), `DELETE /api/admin/holidays/{date}`.

## 4. Security and identity

- A middleware refuses every request whose source address isn't the
  Supervisor's ingress proxy (`172.30.32.2`) or loopback; that is what makes
  the `X-Remote-User-*` headers trustworthy. No user id → 401.
- Admin = the user id or the login name is in `admin_users` (case-insensitive).
  Display names are never matched. Nobody is auto-promoted; while the list is
  empty every page shows the "No admin yet" banner.
- "How the app sees you" (`GET /api/whoami`) echoes only the identity headers
  and a count of admin entries.
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'
  data: blob:; connect-src 'self'; …` — no inline scripts, no eval, no outside
  hosts. Request bodies over 64 KB are refused (except the import).
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

A backup must contain the migration-1 tables and must not come from a newer
schema; it is validated, swapped in and migrated.

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
- `GET /api/me` gives the child's play time and limits; for admins it lists
  children with 5 minutes or less left (`lowTimeChildren`).

## 8. Home Assistant

- Time zone from `GET /api/config` at startup; UTC if it can't be read.
- People and phones: the shared `ha_people.py`, refreshed every 5 minutes.
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
  turning the switch off posts `unavailable` once.

## 9. Frontend

- `index.html` loads `theme-boot.js`, `style.css`, `backnav.js`, the game
  files in the order of spec/GAMES.md, then `app.js`, `play.js`, `admin.js`,
  all with `?v=<version>`.
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
- The game pauses on `visibilitychange` (hidden), `pagehide`, the pause
  button/P/Esc and the back gesture: a running game is the top "layer" for
  `backnav.js`, so Back pauses it first, then leaves the game, then the app.
- The page measures active (unpaused) time itself for the heartbeats and uses
  the game's `result.seconds` for the score.

## 10. Tests

`python -m unittest discover -s tests` (API, auth, first run, admin, children
and limits, scores and leaderboard, sensors with a fake Home Assistant,
storage and migrations, levels and the builder with a fake model, packaging) and `node --test tests/js` for the game
logic, run from `tests/test_games_js.py` when Node is installed.

## 11. Levels and AI-made levels

### 11.1 What it does

- Brick Breaker (both modes share one list) and Snake's **Maze** mode play
  through a **level list**: ten built-in levels each (`level_data.py`, the
  same as `LAYOUTS` / `MAZES` in the game files), then any made by an AI model.
- The model is the household's own (Ollama on the network) or a remote one (an
  OpenAI-compatible service or Anthropic Claude), set up as in Finance
  Dashboard (`ai_client.py` is the same client, without images).
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

## 13. Playing together from two phones (the next build — not built yet)

Two people in the household, each signed in to Home Assistant on their own
phone (or computer), play one game together. It comes in three steps, each a
release of its own; this section is the plan. The tables and routes named here
don't exist yet.

### 13.1 What it does

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

### 13.3 Step 1 — Race

The same single-player game on both phones, with the same seed (the same
traffic, the same pieces, the same sums), side by side: each plays their own
game, and a bar at the top shows the other's score, level and whether they are
still playing, updated a few times a second. It doesn't need lockstep, so
delay doesn't matter.

- Games: Lane Racer, Flap, Falling Blocks, Merge, Number Dash, Colour Memory,
  Memory Cards, Tap the Mole, Mines, Rocks, Sky Defenders, Road Hop (every
  single-player game whose randomness comes from the seed; Brick Breaker and
  Snake too).
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
- A duel game registers `players: 2` and `lockstep: true`; its rules take
  inputs per player (`press(s, action, down, player)`), are already
  deterministic, and gain `checksum(s)`.
- Score: each player's own (Snake Duel: food, rounds and matches won for that
  player; Paddle Duel: as now from each side). Honest-score limits as now,
  checked for each player.

### 13.5 Step 3 — Turn by turn

Wave 7's family games (Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and
Boxes, Sea Battle) are played this way from the start, and Chess from wave 8:
a move is sent to the server, **checked there** (each game has a small Python
rules module that knows the legal moves — the only games whose rules run on the
server), stored, and the other player gets a notification "Your move in Four in
a Row against Asha" (at most one every 15 minutes per match, none in quiet
hours). A match can last days; both players see it in "Your games" on the
Games page. Either can resign; 7 days without a move ends it as a loss for the
one who didn't move.

### 13.6 Data (a new migration)

- `matches`: id, game, mode, kind (`race` | `live` | `turns`), seed, level list
  (JSON, as for sessions), practice, status (`invited` | `playing` | `paused`
  | `done` | `declined` | `expired` | `cancelled`), created_by, created_at,
  started_at, ended_at, end reason (`finished`, `left`, `timeout`,
  `out_of_step`, `resigned`, `time_limit`), winner (user id, NULL for a draw).
- `match_players`: match, user, seat (1, 2), invite status, session id (the
  usual play session, so limits and play time work unchanged), score, level,
  seconds, result.
- `match_moves` (turn-by-turn only): match, number, seat, move (JSON), at.
- Live inputs aren't stored (only kept in memory while the match runs).
- Housekeeping: invites expire; live matches with nobody connected for 60 s end;
  finished matches are kept like scores (Keep scores for).

### 13.7 API

- `GET /api/players?game=` — who can be invited now (with reasons for the rest).
- `POST /api/matches {game, mode, kind, opponent, practice}` → 201 (invite
  sent); `GET /api/matches` (mine: waiting for me, sent, playing, recent);
  `POST /api/matches/{id}/accept | decline | cancel | resign`.
- `GET /api/matches/{id}/live` (WebSocket) — messages: `hello` (seat, seed,
  mode, levels, delay), `ready`, `start`, `input` {update, actions}, `state`
  {score, level, over} (race), `checksum` {update, value}, `pause` / `resume`,
  `peer` (connected, waiting, gone), `end` {reason, results}.
- `POST /api/matches/{id}/result {score, level, seconds}` — each player's own;
  stored when both have reported (or the other has gone), checked against the
  honest limits; live duels' results must match each other.
- `POST /api/matches/{id}/move {move}` (turn by turn) — 409 if it isn't your
  turn, 422 if the move isn't legal.
- Notifications use the existing notify settings (each person's linked phone,
  the "Receive notifications" preference); a new App setting **Invites by phone
  notification** (on) can turn them off household-wide.

### 13.8 Controls and screens

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

- Lockstep: two game instances fed the same inputs with random delays and
  reordering through a fake relay stay identical (checksums) for every duel
  game; a dropped peer pauses then ends the match.
- Server: invites (who can be invited, children's limits, one live invite at a
  time, expiry), accept/decline/cancel, results (both agree, honest limits,
  practice), head-to-head totals, turn-by-turn move checking and turn order,
  notifications (fake Home Assistant), the WebSocket relay with two test
  clients.
- Packaging: `wsproto` in requirements; no outside service is contacted.

## Planned games

Every game is a module in `static/games/` behind the same start screen, pause,
scores and limits; adding one means adding its files, one registry entry, one
entry in `games.py`, its level list (`level_kinds/<game>.py`, §11.9) and a line
in this file. Waves 1–3 are done (Falling Blocks, Paddle Duel, Lane Racer,
Flap; Mines, Merge, Colour Memory, Memory Cards, Tap the Mole, Number Dash;
Tank Battle, Sky Defenders, Rocks, Road Hop, Snake Duel); the rest will be
added in this order, after playing together (§13):

4. **Sudoku** (Easy to Expert, notes, number lines, hints), **Solitaire**,
   **Word Guess**, **Word Search**.
5. **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander**,
   **City Defense**.
6. **Slide Puzzle**, **Lights Out**, **Picture Logic**, **Tile Match**,
   **Code Breaker**, **Type Rain**.
7. Turn-by-turn family games: **Four in a Row**, **Tic-tac-toe**, **Checkers**,
   **Reversi**, **Dots and Boxes**, **Sea Battle** (same screen, or from two
   phones turn by turn as in §13.5).
8. **Ludo**, **Snakes and Ladders**, **Carrom**, **Chess**.

Not planned: a daily challenge, a full-screen TV leaderboard, a "game of the day".
