# Household Arcade — how it works

Classic arcade games for everyone in a Home Assistant home, with personal
bests, a household leaderboard and play-time limits for children. This file
describes the app as it is now; playing together from two phones (§13: races,
live duels and turn by turn are built) and the games still to come are at the end.

## 1. Scope

- Forty-two games, written from scratch (no ROMs, no emulators, no commercial
  names, artwork, music or level layouts): **Snake**, **Brick Breaker**,
  **Falling Blocks**, **Paddle Duel**, **Lane Racer**, **Flap**, **Mines**,
  **Merge**, **Colour Memory**, **Memory Cards**, **Tap the Mole**, **Number
  Dash**, **Tank Battle**, **Sky Defenders**, **Rocks**, **Road Hop**, **Sudoku**, **Word Guess**, **Word Search**,
  **Snake Duel** (two players on one screen, against the computer, or live on two phones), and wave 5's
  **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander** and **City Defense**, and wave 6's **Slide Puzzle**,
  **Lights Out**, **Picture Logic**, **Tile Match**, **Code Breaker** and **Type Rain**, and wave 7's board games
  **Four in a Row**, **Tic-tac-toe**, **Checkers**, **Reversi**, **Dots and Boxes** and **Sea Battle** (against
  the computer, two on one screen, or turn by turn from two phones, §13.5), and wave 8's **Ludo** and **Snakes and
  Ladders** (2–4 players: against the computer, everyone on one phone, or turn by turn from 2–4 phones with the
  server's dice), **Carrom** (against the computer, two or four on one screen, or live from two phones taking turns,
  §13.4) and **Chess** (against the computer, two on one screen, or turn by turn from two phones).
- Every game except the puzzle and word games made from the seed (Sudoku, Word Guess, Word Search, Slide Puzzle, Lights
  Out, Picture Logic, Tile Match, Code Breaker) and wave 7's and wave 8's board games (whose "level" is the
  computer's, the mode)
  has a level list that an AI model can add to (§11); the modes
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
  live.py         live duels: the relay and referee for lockstep play on two phones (rooms in memory, §13.4)
  turns.py        turn by turn: starting, moves checked by the rules, dice, resigning, 7 days, scores, your-move
                  notifications (§13.5)
  rules/          the server's rules of the turn-by-turn games, one module each (fourrow, tictactoe, checkers,
                  reversi, dots, seabattle; ludo, snakes, chess) and base.py (§13.5)
  common/         shared Python (copies of the repository's common/): ha_notify, ha_people, whoami, ha_client,
                  ha_time, housekeeping, auth_core, db_core, settings_core, people_admin, web_security,
                  backup_core, sensor_publisher, ai_client
  routers/        me.py, prefs.py, play.py, users.py, admin.py, levels.py, together.py (+ the live WebSocket)
  static/
    index.html style.css app.js play.js admin.js together.js
    common/       shared browser files (copies): theme-boot.js themes.css ui.js settings.js settings.css
                  people.js backnav.js whoami.js
    games/        the games (see spec/GAMES.md): kit.js sound.js registry.js lockstep.js, then <game>-logic.js
                  (rules) and <game>.js (drawing) for each of the 42 games; boardkit.js (wave 7's shared board-game
                  logic and drawing) before the wave 7 files, dicekit.js (Ludo's and Snakes and Ladders' shared dice
                  logic and drawing) before theirs; chess-worker.js (the chess computer in a Web Worker)
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
| `notify_turns` | `true` | "Your-move notifications" for turn-by-turn matches (§13.5) |
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
7. `users.game_prefs`, `daily_challenges`, `play_sessions.daily` (wave 4).
8. `match_moves`, `matches.options / state / turn_at`, `match_players.notified_at / notify_due / left_at` (§13.5,
   §13.6).

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
  | Bubble Pop | 25,000,000 | 1,400 | 2,000 |
  | Gem Swap | 50,000,000 | 5,500 | 3,000 |
  | Tower Stack | 3,000,000 | 450 | 500 |
  | Runner | 5,000,000 | 500 | 500 |
  | Lander | 5,000,000 | 350 | 1,000 |
  | City Defense | 5,000,000 | 300 | 2,500 |
  | Slide Puzzle | 10,000 | 10,000 | 10,000 |
  | Lights Out | 10,000 | 10,000 | 10,000 |
  | Picture Logic | 10,000 | 10,000 | 10,000 |
  | Tile Match | 10,000 | 10,000 | 10,000 |
  | Code Breaker | 12,999 | 13,000 | 13,000 |
  | Type Rain | 5,000,000 | 600 | 1,000 |
  | Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle | 1,000 | 1,000 | 1,000 |
  | Ludo, Snakes and Ladders, Carrom, Chess | 1,000 | 1,000 | 1,000 |

  The rates are what the rules allow at their fastest (a piece every 10
  updates, a point every half second, the top road speed, a gate every
  48 updates), so real play never reaches them; the tests play each game
  with a quick bot and check it stays inside. The puzzles that score once
  when solved (Sudoku, Word Guess, and wave 6's Slide Puzzle, Lights Out,
  Picture Logic, Tile Match and Code Breaker) can reach their most at once, so
  their base is their most: Slide Puzzle and Lights Out score 10,000 − moves
  (Lights Out also − 5 a hint), Picture Logic and Tile Match 10,000 − the
  counted seconds, Code Breaker 1,000 × (rows left + 1) + up to 999 for speed
  (12 rows at most); an unsolved one scores 0 and keeps nothing
  (`unfinished_zero`). Type Rain: a word ≤ 10 letters × 10 × 3 with words ≥ 40
  updates apart (450 a second) and a stage 200 (≥ 5 words and a 2 s break). Wave 7's board games: a win scores the
  level's base (Easy 100, Medium 250, Hard 500; 500 against a person from two phones) plus a bonus for how well
  (Four in a Row 10 an empty place left, Tic-tac-toe 25 an empty square, Checkers 30 a piece left, Reversi 5 a disc
  more, Dots and Boxes 20 a box more, Sea Battle 15 a square of your fleet unhit), at most the base again; a draw a
  quarter of the base; a loss and two players on one screen 0 (`unfinished_zero`: not kept; the shell says "Not a
  win — not saved" / "Two players on one screen — not saved" from the result's `stats.notSaved`). A win can come at
  once, so the base is the most. A two-phone match's scores are worked out by the server (§13.5), never posted.
  Wave 8 the same way: Chess as wave 7 (10 a point of material left: pawn 1, knight and bishop 3, rook 5, queen 9);
  Ludo against the computer 100 a computer player + 10 a token of theirs not home, from phones 500 + 25 a token;
  Snakes and Ladders 100 against the computer, 500 from phones, + 5 a square the nearest other still had to go;
  Carrom the level's base (100 / 250 / 500; 500 live) + 20 a board point (the other side's coins left, + 3 for the
  queen); always at most the base again. Everyone on one phone (Ludo, Snakes and Ladders), two or four on one
  screen (Carrom) and a loss score 0 and keep nothing; a Carrom board ended before it was won says "Board not
  finished — not saved" (also when ended from the pause screen: the session's `unfinishedNote()`).
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
- Turn by turn (§13.5): the match's page is an ordinary play session (`POST /api/sessions {game, matchId}`, one per
  visit), so **every second the page is open and not paused counts as play time, whoever's move it is**, and opening
  it is refused like starting a game. Every move checks the child's limits again (allowed games 403; quiet hours or
  no time left 409 with the reason): they can see the board but not move. Invites to a turn-by-turn game ignore
  time and quiet hours (the match lasts days); allowed games still apply.
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
  `common/whoami.js`, the game files in the order of spec/GAMES.md (`lockstep.js` right after `registry.js`), then
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
| Bubble Pop | Puzzles | `puzzle` | 3–8 rows of 8 bubbles (colours 1–6, all hanging from the top row), colours, shots per ceiling drop |
| Gem Swap | Levels | `moves` | moves, points to reach (≤ 80 a move), kinds of gem, an 8 × 8 pattern of locks (≤ one per two moves) |
| Tower Stack | Towers | `towers` | floors, starting width, speed, speed-up a floor (top speed ≤ 5), whether perfect drops grow the block |
| Runner | Courses | `courses` | speed, speed-up, a pattern of segments (boxes, pits, bars, fliers, coins; hazards far enough apart to land and jump again) |
| Lander | Levels | `levels` | 13 ground heights, pads ×1–×5 (flat), start, drift, gravity, wind, fuel (enough for a full stop) |
| City Defense | Waves | `waves` | missiles, speed, splitting missiles, fliers, ammo a base (enough for what comes) |
| Type Rain | Stages | `stages` | words (5–40), speed, gap between words (≥ 40 updates), shortest and longest word (1 and 1: single letters; else 3–10), slow enough to type the longest |

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

## 13. Playing together from two phones (steps 1, 2 and 3 are built)

Two people in the household, each signed in to Home Assistant on their own
phone (or computer), play one game together. It comes in three steps, each a
release of its own. **Step 1 (Race, §13.3) is built**: invites, the match tables
(migration 6), the live link with its long-poll fallback, the race screen,
Rematch, head to head and the App setting *Invites by phone notification*.
**Step 2 (live duels, §13.4) is built**: lockstep play of Snake Duel, Paddle
Duel and Tank Battle's *Two phones* modes (`games/lockstep.js`, `app/live.py`).
**Step 3 (turn by turn, §13.5) is built** with wave 7's board games: moves checked by
the server's own rules (`app/rules/`, `app/turns.py`, migration 8), "Your games", your-move
notifications, and the 2–4 player plumbing (seats, starting with those who joined, server
dice, the computer standing in for a player who left) that wave 8's Ludo and Snakes and Ladders use; Chess is
turn by turn too. **Live turn-taking** (§13.4, Carrom's *Two phones* mode) is built on the live duels' relay.
The sections below say "Built" where they describe what exists.

### 13.1 What it does

*Built for races (§13.3) and live duels (§13.4). In a race a running game always
finishes, as everywhere (§7), and each child's own limits decide whether they
can start or join; in a live duel a child out of time — or whose quiet hours
begin — ends the match for both, as a draw (both scores so far are kept).*

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

**Built (live duels).** The same route, `GET /api/matches/{id}/live`, carries a
live duel's lockstep messages both ways once its invite has been accepted (before
that, and for races, it pushes the match picture as above). The browser side is
`Together.liveLink` (`together.js`): a WebSocket that is opened again after a drop
(after 0.5, 1, 2 and 4 s), then plain HTTP — `POST /api/matches/{id}/live
{since, msgs, wait, v}` (§13.7) — with one request waiting for messages and
another sending this phone's own every 30 ms. Every (re)connection starts with
`hello {since}`; the phone pings every 2 s (which also measures the round trip
for the input delay).

- **The server is a relay and referee, not a game engine** (`app/live.py`). It
  never runs the games (they are JavaScript in the browser). It passes inputs
  between the two phones in order, keeps the match's record, compares the
  phones' checksums and their reports of the end (they must agree) and applies
  the usual honest-score limits to each player's own score.
- **Lockstep.** The games run in fixed updates (60 a second) from a seed, so
  both phones run the *same* game: both get the seed, the mode and the level
  list from their session (`POST /api/sessions {game, matchId}`, which also
  says the phone's `seat`); each phone sends only its own inputs, stamped with
  the update they apply to; an input is played `delay` updates after it was made
  (the server chooses the starting delay when both phones are ready: the time
  phone to phone — half of each phone's round trip — in updates, plus 4 for
  sending, drawing and jitter: 4 to 6 at home, at most 16, 0.27 s), so both
  phones apply every input at the same update and the games stay identical. A
  phone that hasn't got the other's inputs for an update waits (the game freezes
  briefly; the bar says "waiting…"), and a phone that has fallen behind plays
  one extra update a frame to catch up. **The delay adapts**: the
  server's delay is the least; each phone raises its own input delay by a step
  when it has had to wait at 3 or more updates in a second (two steps from 8),
  at most every half second, up to 30 (half a second), and lowers it a step
  after 10 clean seconds — a slow link (Home Assistant Cloud, a phone away from
  home) costs a little input lag rather than a game that stops and starts. The
  two phones' delays add up: a round trip has to fit in `delay₁ + delay₂ − 2`
  updates for the game to run at full speed. After 10 s of silence both are paused,
  after 60 s the match ends (§13.4). Every second each phone sends a checksum of
  its game state; if they ever differ the match ends as "out of step" with no
  result (and a log line), which the tests make sure never happens.
- **Pausing**: either player's pause (or their phone hiding the app) pauses
  both. Saving a live match for later is not offered.
- Measured on localhost through a WebSocket-forwarding ingress proxy (the
  browser test): the delay comes out at 4–5 updates. Not measured yet: the
  companion apps and Home Assistant Cloud (the HTTP fallback is what makes that
  safe to ship).

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
Number Dash, Sky Defenders, Rocks, Road Hop, Sudoku, Word Guess, Word Search
(their own rules), and wave 5's Bubble Pop, Gem Swap, Tower Stack, Runner,
Lander and City Defense (`"race": {"rule": "score"}`, also in `RACE_DEFAULT`:
the same level list and the same sequence from the seed; the higher score wins,
and Lander's score includes half the fuel left, so of two equal landings the one
with more fuel left wins — no separate tiebreak), and wave 6 by their `race`
keys: Slide Puzzle and Lights Out `{"rule": "fastest", "tiebreak": "score"}`
(solved first wins; solved in the same second, the higher score — fewer moves —
wins; `decide` takes the score as the third key only with this tiebreak, so
other `fastest` games are unchanged), Picture Logic and Tile Match
`{"rule": "score"}` (10,000 − the counted seconds, hints and shuffles included),
Code Breaker `{"rule": "score"}` (fewer rows, then faster) and Type Rain
`{"rule": "score", "tiebreak": "faster"}` (the same words in the same order
from the seed and the clock, never from the typing; the same score: the shorter
game, i.e. more words a minute). Lights Out's Hints option is off in races
(`offInRaces`). Not raced: Paddle Duel and Tank
Battle (left out of the plan's list), Snake Duel (two players on one screen) —
the three are played live on two phones instead (§13.4).
The shell's hooks: the game definition's `race` flag (default true unless
`players: 2`), the kit session's `status()` (score, level, over, paused;
nothing changes in any game file), the game page's race bar, count-in and
result card (`play.js`), and a `GET /api/games` field `race` that is the
server's decision. Every raced game is checked in Node: two phones with the
same seed and the same inputs end in an identical state (`race.test.js`; wave 5
also in its level-list modes and with the match's own list, `wave5.test.js`;
wave 6 in two modes each and Type Rain with the match's stage list, `wave6.test.js`).
Wave 5's rules also give the same numbers on every browser: they use only
`+ − × ÷` and exact `Math` functions (Bubble Pop's and Lander's sines come from
their series, not `Math.sin`), checked with the other `Math` functions made to
throw; wave 6's rules (the boards, heaps, pictures, codes and falling words too)
are checked the same way.

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
    breaks a tie). *Built with wave 6* (Picture Logic and Tile Match: the
    counted time, penalties included, as Sudoku).
  - **Code Breaker** — the same hidden colours; fewer rows wins, then the
    faster time. *Built with wave 6.*
  - **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander**,
    **City Defense** — the same level and the same sequence; the higher score
    wins (Lander: landed with more fuel left). *Built with wave 5.*
  - **Type Rain** — the same words in the same order; higher score wins, words
    per minute breaks a tie. *Built with wave 6* (the tie goes to the shorter
    game).
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

### 13.4 Step 2 — Live duels (built)

**As built.** A live duel is a `matches` row of kind `live` for one of a game's
*live modes* (`games.py` `live.modes`; the game file registers `lockstep: true`
and the same `liveModes`):

| Game | Mode | | Each player's score | The match's winner |
|---|---|---|---|---|
| Snake Duel | `phones` Two phones | one match on an arena from the seed (the list's), seat 1 green, seat 2 blue | food 10, round 100, match 500 × min(arena, 10) | the match (a drawn match: a draw) |
| Paddle Duel | `phones` Two phones | seat 1's paddle at the bottom, seat 2's at the top — seat 2's phone draws the court turned half-way round (x → 240 − x, y → 320 − y) and turns its left/right and finger round the same way; first to 7; whole-number physics (1/256 px, a sine table, no `Math.sin`) | return 10, point 100, match 1,000 | the match |
| Tank Battle | `together` Two phones · Together | both tanks guard one flag against 1½ × the arena's enemies (the arena list); own lives; friendly shells stop harmlessly; over when the flag falls or both are out of lives | a tank 100 to whoever hit it, an arena 500 to both | the higher score (a tie: a draw) |
| Tank Battle | `against` Two phones · Against each other | no enemies; four built-in arenas the same turned half-way round (`VERSUS_LEVELS`, not the AI list), a flag each; seat 2's phone draws the arena turned round (its flag at the bottom); a round: their flag or all their lives, at most 2 minutes (then drawn); a match: first to 2 rounds, at most 5 | a hit 100, a round 300, the match 500 | the match (all drawn: a draw) |

Single-player Tank Battle, Paddle Duel's computer modes and Snake Duel's other
modes are unchanged (the same random numbers in the same order; checked
against the previous rules over 175 random games when this was built).

- **Invite and start**: `POST /api/matches {kind: "live"}` (or no `kind`: a
  live mode means live) → the invite, notification, Waiting for you, Cancel and
  one-at-a-time rules of a race. Accepting opens the match's room. Each phone
  starts its session (`matchId`; children's limits, allowed games and quiet
  hours checked as for any session; a live mode without a match is refused,
  422), opens the live link, measures its round trip and says `ready`; when
  both have, the server sends both `start {delay, inMs: 3000}` and both count
  in 3-2-1 and play update 1. A phone not ready within 2 minutes of the
  accept: the one that was ready wins (`left`), neither: no result.
- **The rules** take inputs per player: `press(s, action, down, player)` (player
  0 or 1; `down` is 1/0 for a button, or a small whole number for `aim` /
  `steer`), stay deterministic, and gain `checksum(s)` (FNV-1a over the state
  that matters, an unsigned 32-bit number) and `report(s)` → `{scores: [p1,
  p2], levels, winner: 1 | 2 | 0}`; `result(s, player)` is that player's own.
  Nothing in live play uses `Math.sin`/`cos`/`pow`/`random`/`Date` (engines may
  differ in their last digit; the tests run the rules with those replaced by
  functions that throw).
- **Each phone's inputs**: keys, buttons, swipes and controller buttons are
  turned into the game's actions on the phone (Snake Duel: a direction; Paddle
  Duel: left/right held, `fire` to serve, `aim` = the finger's x on the court;
  Tank Battle: the arrows and `fire` held, `steer` = 1 + the direction toward a
  finger, 0 when it lifts; a tap on your own tank fires). The kit
  (`createSession` with `opts.live`) hands them to `ArcadeLockstep`
  (`games/lockstep.js`), which plays each at update + delay on both phones,
  seat 1's before seat 2's (each in the order made), at most 6 per player per
  update (an `aim`/`steer` changed again before it is sent replaces the last).
- **Ending**: the game ends on the same update on both phones; each posts its
  own score to `POST /api/scores` as any game (honest-score limits, Practice,
  under 3 s, personal bests, records), with `report = {tick, sum, scores,
  levels, winner}` (its view of the end). When both are in and identical, and
  each posted score is the one both reported for that player, the match is
  `finished` with the reported winner; otherwise it is `out_of_step` with no
  result. One phone's report alone decides after 60 s if the other never sends
  one. Other ends: **Give up** (pause screen) or leaving the game page
  (`POST /api/matches/{id}/resign`, also sent on `pagehide`) → `resigned`, the
  other wins; a phone silent for 60 s → `left`, the other wins (both: no
  result); a child out of time or into quiet hours (checked every 5 s by the
  server, `together.live_watch`) → `time_limit`, a draw; a pause over 5 minutes,
  or the app restarting mid-match (the room is gone) → `timeout`, no result.
  After `left`, `resigned` and `time_limit` the remaining phones post their
  score so far (a game ended early, §12); after `out_of_step` and `timeout`
  the sessions end without a score.
- **Protocol** (`app/live.py`; the message list and the checks are in §13.7):
  - *Ticks*: updates are numbered 1, 2, 3 …; ticks 1 … delay − 1 have no inputs.
    A phone that has played tick `t` has its inputs final up to `t + delay − 1`
    (its own, adapted delay) and says so in `upto`, with the tick it is at in
    `at` (the other phone paces its catching up by it); it may play tick `k`
    only when the other's `upto ≥ k`. So a phone can never speak for more than
    `delay − 1` ticks beyond the other's word — the server refuses an `upto`
    above the other's + 30 (the most a delay can be). What a phone has to say
    goes out once a frame (one message however many updates the frame played),
    and only when there is something new: an input, a moved-on tick, a checksum.
  - *Ordering and idempotence*: a phone's input messages carry `seq` 1, 2, 3 …;
    the server accepts only `seq` = last + 1 (an older one is answered with
    `ack` again and dropped; a later one with `error {why: "gap", inSeq}`), so
    each phone's inputs reach the other once, in order. The server numbers what
    it sends each phone (`n` 1, 2, 3 …) and keeps it until that phone
    acknowledges it (`hello.since`, `ack` on its messages); the phone takes
    numbered messages in order, holding one that came early until the gap is
    filled, and ignores repeats. After a reconnection `welcome {inSeq}` tells
    the phone which of its messages to send again (same `seq`). The lockstep
    itself also ignores a repeated `seq` and holds an early one.
  - *Limits*: 2,048 bytes a message (a WebSocket closes on a bigger one; the
    HTTP fallback answers `error "too big"` for it and takes at most 64 a
    request); 90 messages a second with a burst of 180 (a phone sends at most
    one an update and a ping every 2 s; more → `error "slow down"`, dropped);
    32 inputs a message, 8 a player a tick; only the game's actions with values
    in their range (`games.py` `live.actions`); an `upto` may move at most 600
    ticks at once; a checksum only every 60 ticks, for a tick already played
    by that phone (≤ `upto − delay + 1`). 30 refused messages close the
    connection. At most 6,000 numbered messages wait for a phone (more: it has
    left).
  - *Who*: the WebSocket checks the source address, the Origin and the identity
    as for a race; the seat comes from the person, never from the message; a
    third person gets nothing (the socket is closed, the HTTP fallback is 404).
  - *A phone gone or stalled*: any message (pings every 2 s) counts as heard.
    Silent 10 s while playing → both get `pause {by, why: "lost"}` (only that
    phone coming back resumes it); silent 60 s → `left`.
- **Children**: as any session at the start; the server ends the match as a
  draw when a child's time runs out or their quiet hours begin (heartbeats keep
  the time used up to date every 15 s).

**Live turn-taking (built, wave 8: Carrom's `phones` *Two phones (live, taking turns)*).** The same relay, room,
link, invite, count-in, pause, resign, children's checks, end reports and results as a duel, for a game whose
`games.py` `live` has `turns: true` (and a `timeout_shot`), registered `lockstep: "turns"`:

- *A shot is one input*: `[place 0–1000 along the shooter's baseline, angle 100–1700 (tenths of a degree in the
  shooter's own view, 900 straight ahead), power 1–100]` (`live.actions.shot`). The phone whose turn it is sends
  `in {seq, upto: k, ev: [[k, "shot", value]]}` with `k` = shots so far + 1; the server checks the seat is the
  mover, `k` is exactly the next number and the value is in range, then relays it to **both** phones (`in {seat,
  seq, upto: k, ev}`) — a phone plays its own shot only when it comes back, so the server's order is the only order
  and there is no input delay (`start {delay: 0, inMs, turns: true, first}`; `first` = the seat that breaks,
  `seed % 2 + 1`).
- *After a shot* has stopped moving each phone sends `in {seq, upto: k, ev: [], sum: [k, checksum], next}` (`next`:
  the seat whose shot it is now, 0 when the board is over). The checksums are compared as in a duel (different →
  `out_of_step`, no result); the first `next` for that shot sets the mover.
- *30 s a shot*: the clock runs from the first report of the last shot (or the count-in's end); at 30 s + 2 s
  grace the server plays the game's weak `timeout_shot` (`[500, 900, 12]`: the middle, straight ahead, gently) for
  the mover — `in {seat, seq: 0, upto: k, ev, timer: true}` to both — and a shot of theirs arriving after that is
  acknowledged (`ack {seq, late: true}`) and dropped. Paused time doesn't count; the picture's `live.turns =
  {shots, mover, left}` shows the seconds left.
- *Stored*: each shot goes into `match_moves` (`{shot: value, timer}`, when the match ends), for the record.
- *Ending*: the board's end on both phones (identical, as the checksums show) → each posts its score with the
  report (`tick` = shots played) as in a duel; the side that pockets all its coins wins, or after 300 shots the side
  with more points (spec/GAMES.md, "Carrom").
- The kit (`createSession` with `opts.live`, `ArcadeLockstep.createTurns`) steps the board freely while a shot moves
  and waits only for the next shot; the second phone draws the board turned round, so each player's own baseline is
  at the bottom. The plug-in points are in spec/GAMES.md ("Live duels").

The plan, as before:

- **Snake Duel** first (both modes become: against the computer, two players on
  one screen, **two phones**), then **Paddle Duel** (a new "two phones" mode:
  each player sees their own paddle at the bottom — the second phone draws the
  court upside down), then **Tank Battle** (two tanks: *together* — protect one
  flag — or *against each other* in an arena with a flag each).
- **Carrom** — *built (above)*: two players, live, taking turns — each shot is
  the striker's position, aim and power, sent to both phones, and both phones
  play the same shot with the same physics. The physics use whole-number
  (fixed-point) maths, not floating point, so every phone gets exactly the
  same result; a checksum after each shot confirms it (out of step ends the
  match with no result). A player has 30 s to take a shot (then a weak shot is
  played for them). Doubles (four players, two teams) are on one screen only.
- A duel game registers `players: 2` and `lockstep: true`; its rules take
  inputs per player (`press(s, action, down, player)`), are already
  deterministic, and gain `checksum(s)`.
- Score: each player's own (Snake Duel: food, rounds and matches won for that
  player; Paddle Duel: as now from each side). Honest-score limits as now,
  checked for each player.

### 13.5 Step 3 — Turn by turn (built)

**As built.** A turn-by-turn match is a `matches` row of kind `turns` for one of a game's *turn modes*
(`games.py` `turns.modes`; the game file registers `turns: true` and the same `turnModes`): wave 7's *Two phones
(turn by turn)* mode (`phones`) of Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes and Sea Battle, and
wave 8's Chess (the same mode) and Ludo and Snakes and Ladders (*Phones (turn by turn, 2–4)*, `phones`).

- **The server's rules** (`app/rules/<game>.py`, the only games whose rules run on the server): `PLAYERS` (least,
  most), `OPTIONS` (the match's choices: Dots and Boxes' `size` 3/4/5, Sea Battle's `fleet` classic/small), `DICE`
  (sides of the server's die, 0 = none), `new(seed, players, options)` (who moves first: `seed % players`),
  `to_move`, `moves`, `apply(state, seat, move) → (state, record)` (raises `Illegal`), `result`, `score`, `view`
  (what a seat may see), `visible` (a stored record as a seat may see it), `digest` (compared with the JavaScript
  rules), `computer` (a stand-in's move) and, optionally, `forced(state, seat)` (the move a roll leaves no choice
  about, or None). States are JSON, never changed in place. The JavaScript rules
  (`<game>-logic.js`, via `boardkit.js`) are the same rules — used to draw the board, for the computer and on one
  screen — and the tests compare the two move by move on 2,400 random games (legal moves before each move, the
  state after it, and probes that may or may not be legal).
- **Invite** (`POST /api/matches`, kind from the mode): 1 to `most − 1` others (`opponents`), the match's `options`
  (the inviter's start-screen choices, checked against the rules' `OPTIONS`), Practice as for races. It lasts **7
  days**; a person may have several turn-by-turn invites and matches at once, and they don't count as "in a match"
  for races, live duels or anything else. A child can be invited whatever their time or quiet hours (moves check
  those); a game that isn't one of theirs can't. The notification and "Waiting for you" are the race's.
- **Start**: when everyone invited has joined; or the inviter starts with those who have (`POST
  /api/matches/{id}/start`, ≥ the rules' least); or, when the last invitee answers or the 7 days run out, with
  those who joined if they are enough (else declined / expired). Those who didn't join are dropped and the seats
  are numbered 1… in invite order. The first to move is told it's their move (not the person who just joined).
- **A move** (`POST /api/matches/{id}/move {move, n?, sessionId?, activeSeconds?}`): 404 for someone not in the
  match; 409 when the match isn't being played, it isn't your move, you've left, `n` (the moves the phone has
  seen) isn't the server's count, or another phone's move came first (two moves can't get one number); 422 for a
  move that isn't legal or isn't an object (≤ 2 KB); children's limits (§7) on every move. The record is stored in
  `match_moves` with the server's dice roll (if any); seats the computer plays move at once after it; the turn's
  clock (`turn_at`) restarts when the player to move changes. `activeSeconds` is the page's time so far (as a
  heartbeat), so a match that ends with the move has the mover's time.
- **Dice** (`POST /api/matches/{id}/roll`, games with `DICE`): the server rolls for the player whose move it is
  (409 otherwise, or for a game without dice) and keeps it in the state (`_roll`); asking again gives the same roll
  until the move is made; a move needs a roll (409 "Roll the dice first"), the rules read it from `_roll` — a phone
  can never send its own — and it is stored with the move (`match_moves.dice`). *Wave 8*: when the roll leaves no
  choice (the rules' `forced`: Ludo's pass — no token can use it, or a third 6 — or tokens that would all end up
  alike; every Snakes and Ladders roll) the server plays that move at once with the roll, so nobody has to tap to
  pass. The answer is `{roll, seat, moved, picture}` (`moved`: the roll was played; `picture`: the turn-by-turn
  picture after it, so the phone draws the move without another request); the next player is told it's their move.
- **Hidden information** (Sea Battle): the server keeps both fleets in `matches.state`; a phone gets only `view`
  for its seat — its own fleet, the shots both fired and whether they hit, the ships sunk — and the other's
  placing record as `{"placed": true}`. The other fleet is sent only once the match is over (`theirShips`).
- **The picture** (`GET /api/matches/{id}/turns`): seat, players (seat, name, computer, left, result, score),
  every move as this seat may see it, the rules' `view`, `toMove` (seats), `over`, `result` (`{winner: seat | 0 a
  draw | null no result, reason}`), the roll (its own), the seed and options; reading it settles a your-move
  notification still owed. The match picture (`GET /api/matches`, `/api/matches/{id}`, the live link) has
  `turns: {number, lastMoveAt, toMove, myTurn, options, minPlayers, maxPlayers, turnSince}`.
- **Leaving**: **Resign** (`POST /api/matches/{id}/resign`): with two people the other wins (`resigned`); with more,
  the one who left loses (`left_at`, `computer = 1`) and the computer plays their seat (the rules' `computer` with
  the server's dice); when only one person is left, they win. **7 days without a move** (housekeeping, and whenever
  the match is read): the same for whoever should have moved (`timeout`); nobody moved at all (Sea Battle, neither
  placed) → no result.
- **The end**: the best-placed person still playing wins (the rules' winner, then `places`); a draw for everyone
  still in; those who left lost. Each person still in gets the rules' `score` as a normal game of the match's mode
  (not Practice; 0 keeps nothing), with the seconds of their play sessions for the match. A win by resignation or
  timeout scores the base (500), and so does the best-placed person when the rules' winner was a seat the computer
  had taken over (Ludo, Snakes and Ladders: the computer playing a leaver's seat can finish first; the result card
  then says "Ludo: Kabir wins (Meera's seat was the computer's)"). Head to head counts it like any match.
- **Your-move notifications** (`turns.notify_blocking`, App setting `notify_turns` "Your-move notifications", on;
  the person's *Receive notifications*): "Your move in Four in a Row against Asha." (with `Open` → `#/home/turn/<id>`,
  tag `arcade-turn-<id>`) to each person whose move it now is. At most one every **15 minutes** per match and
  person (`match_players.notified_at`); none during a child's **quiet hours**; one that can't go yet stays owed
  (`notify_due`) and housekeeping (every 10 minutes) sends it once it may — unless the person has opened the match
  meanwhile or it's no longer their move.
- **Play time** (§7): the match's page is a play session per visit (`for_session` allows a new one each time; a
  turn-by-turn session never takes a score, `POST /api/scores` is 409); its active time is play time.
- **2–4 players** (built with Ludo and Snakes and Ladders): everything above takes up to four seats (the tests play
  a dummy dice game and both real games with 2, 3 and 4 players: starting when all joined or with those who have,
  server dice, a leaver played by the computer, a timeout with more players). The invite sheet lets the inviter tick
  up to three people when `GET /api/players` says `maxPlayers` > 2. The waiting card counts who has joined ("1
  joined so far — start now with 2 of you, or wait for the rest."); only the inviter sees **Start with n** and
  Cancel; someone who joined sees "You've joined. The game starts when everyone has, or when Asha starts it." and
  Back to games. The bar names the others (🤖 for a seat the computer plays) with a dot each and "Kabir's move".
- **Chess** (wave 8): the server's rules are complete FIDE moves — castling, en passant, promotion (the move names
  the piece, so the phone asks), check, checkmate, stalemate, and automatic draws by threefold repetition, the
  fifty-move rule and insufficient material; checked against the JavaScript rules move by move and both against the
  well-known perft counts (spec/GAMES.md "Chess").

The plan, as before:

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

### 13.6 Data (migration 6; migration 8 for turn by turn)

**Built (live duels):** no new tables. A live duel is a `matches` row with kind
`live`; its room (`live.ROOMS`: seats, phases waiting / countin / running /
paused / ended, each phone's last `seq`, `upto`, checksums and the numbered
messages waiting for it) and the two end reports (`live.REPORTS`) live in memory
only, like the race numbers; they are forgotten 10 minutes after the end
(housekeeping). End reasons: `finished`, `left`, `resigned`, `out_of_step`,
`time_limit`, `timeout`. Each player's score is a normal `scores` row in the
live mode (`phones`, `together`, `against`), so bests and the leaderboard are per
mode as always.

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
  match taking turns), so a dropped connection can catch up. **Built (migration 8)**, with `matches.options`
  (JSON), `matches.state` (the rules' state, server only), `matches.turn_at`, and `match_players.notified_at`,
  `notify_due` and `left_at`; `match_players.computer` marks a seat the computer plays. Finished matches go with
  Keep scores for (their moves with them).
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

**Built (live duels):** `GET /api/players` and `GET /api/games` (with
`liveModes` per game) know live games; `POST /api/matches {..., kind: "live"}`
(a live duel of a mode that isn't a live mode, or a race of a live mode: 422; a
game with no live modes, or a race of a game that isn't raced: 409); the match picture
gains `live` (`{phase, delay, pausedBy, why, seats: {1: {connected, ready}, 2:
…}}`, `null` before the accept) and has `startsInMs: null` (the count-in comes
with `start`); `POST /api/sessions {game, matchId}` answers `seat`;
`POST /api/scores` takes `report` (above); `POST /api/matches/{id}/resign`
(409 unless a live duel being played; the other wins). The live link's messages
(JSON objects, ≤ 2,048 bytes):

| From a phone | |
|---|---|
| `hello {since}` | first on every connection: the last numbered message it has |
| `ping {id, ack?}` | every 2 s; answered with `pong {id}` |
| `ready {rtt}` | its session is open; `rtt` = its median round trip in ms (0–5,000) |
| `in {seq, upto, ev: [[tick, action, value], …], sum?: [tick, checksum], ack?}` | its inputs (above) |
| `pause`, `resume` | for both phones |

| To a phone (numbered `n`, kept until acknowledged) | |
|---|---|
| `in {seat, seq, upto, ev}` | the other phone's inputs |
| `start {delay, inMs}` | count in, then play tick 1 |
| `pause {by, why: "player" \| "lost"}`, `resume` | |
| `end {reason, winner}` | `winner` 1, 2, 0 (a draw) or null (no result) |

and, not numbered: `welcome {protocol: 1, seat, inSeq, delay, phase, last}`,
`ack {seq}`, `pong {id}`, `error {why, inSeq}` (`why`: `gap`, `seq`, `upto`,
`ahead`, `ev`, `tick`, `too many`, `action`, `value`, `sum`, `not started`,
`not a message`, `slow down`), and the match picture `{t: "match", …}` (pushed on
a change and every 3 s). Without a WebSocket: `POST /api/matches/{id}/live
{since, msgs: [≤ 64 messages], wait: ≤ 15 s, v?}` → `{msgs: [the replies, then
the numbered messages after since], match?: the picture when its v isn't v}`
(404 for a match without a room or a person not in it).

The plan, as before:

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
  turn, 422 if the move isn't legal. *Built (§13.5), with `GET /api/matches/{id}/turns`, `POST
  /api/matches/{id}/roll`, `POST /api/matches/{id}/start`, `GET /api/players?game=&mode=` (→ `kind`,
  `maxPlayers`) and `/api/games` `turnModes`.* `POST /api/matches/{id}/roll` (Ludo,
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

**Built (live duels):** a *Two phones* mode's start card has no Play button —
**👥 Play with someone** is the way in, with the line "Two phones: you each play
on your own phone, live, in the same game." The invite sheet, the waiting
overlay ("Live duel"), the count-in (from the server's `start`), the bar (the
other's first name, a connection dot, their score taken from this phone's own
game, and "playing / waiting… / paused / connection lost? / getting ready") and
the result card (with the headlines "Out of step — no result", "Kabir gave up.
You win!", "Play time ran out — a draw", "Ended — no result") are the race's.
The pause overlay says who paused ("Kabir paused the game." — Resume and Give up)
or "Waiting for Kabir… Their phone went quiet." (no Resume). Each phone plays its
own player with every key: W A S D and the arrows are both "mine", a second
controller isn't player 2. The Games page card reads "Your duel with Kabir is
starting" / **Join the duel**.

**Built (turn by turn):** a turn mode's start card has no Play button — **👥 Play with someone** and the line
"Two phones: take turns from your own phones — they needn't be open at the same time." The waiting card adds that
they'll be told and offers **Back to games** (the invite stays out; leaving doesn't cancel it). **Your games** on the
Games page lists every turn-by-turn match going on — the game, the others, "Your move" (first, with the accent) or
"Asha's move", the last move ("3 h ago") — with **Play** / **Open**. The game page opens the match (a play session,
the board from `GET …/turns`), keeps the match's live link (pushes, or the long poll) and fetches the board when the
moves' number changes; the bar shows the other's name, a "here now" dot and "your move" / "their move". A move is
sent to the server and drawn from its answer ("Sending your move…"); a refusal shows on the board's bottom line and
the board is fetched again. Pause: **Resume**, **Back to games (the match waits)**, **Resign** (asks first). The
result card is the race's (the time column: time on the match's page; no levels) with **Rematch** (the same people
and options). A your-move notification opens `#/home/turn/<id>` — also when the app is already open on any page:
the shared back gesture (`common/backnav.js`) treats an address change from a link or a notification (popstate
with an address that isn't its guard entry's) as navigation, not as Back, so the app follows it (wave 8 fix).

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
requirements; no outside service is contacted. Wave 6: `tests/test_wave6.py` (the
game table, the limits worked out from the rules files, Type Rain's stage checks
agreeing with the game file, a race of each game through the API with its own
rule — solved first, fewer moves in the same second, the counted time, rows, the
shorter game — and saving) and `tests/js/wave6.test.js`.

**Built (live duels):** `tests/js/live.test.js` — the lockstep (waiting for the
other's word, delay, seat order, what a message may claim, checksums every 60
ticks, aim coalescing and the per-tick cap, duplicates and early messages, the
resend after a welcome, catching up); for each duel mode the rules are
deterministic with `Math.sin`/`cos`/`pow`/`random`/`Date.now` made to throw, the
checksum follows the state, and each player's score stays inside the honest
limits (25 bot games); two phones (two sandboxes with the real game files and the
kit) through a fake relay with the server's rules stay identical tick for tick
with 5–70 ms delays in any order, 8 % duplicated messages and a 1.8 s dropped
connection, and at 30 Hz against 144 Hz on a slow link; a deliberately changed
game is caught at the next checksum; a phone that hears nothing more waits; the
rules of each two-phone mode (Paddle Duel's court is exactly the same turned
round); both seats draw in every look; seat 2's turned-round inputs; the kit's
end report; the browser link (hello, numbered messages once and in order,
pings, reconnecting, the HTTP fallback, falling back after four failed tries),
the bar and headlines. `tests/test_live.py` — the room (start and delay,
relaying in order once each, every refusal, rate limit, closing, hello and
replay, checksums, quiet / gone / both gone, pauses and the 5-minute limit,
never ready, ends from elsewhere, an unread outbox, reports) and the match
(invites for the live modes, the room and the picture, sessions and seats, a
live mode never played alone, children at the start, the notification,
results that agree / differ / don't match the posted score / break the honest
limits, Practice, one report after a minute, resigning, a phone gone, a
restart, a child out of time and quiet hours as draws, housekeeping, the
WebSocket with two phones, a reconnection, the end pushed, who may connect, a
message too big, the HTTP fallback and its checks). The browser check plays
every mode with two Playwright contexts (two users, desktop + phone, two looks)
through ingress-like proxies that forward WebSockets (and with one phone
without WebSockets).

**Built (turn by turn):** `tests/test_wave7.py` — the game table and honest limits, each rules module (Four in a
Row lines and refusals, Tic-tac-toe, Checkers' compulsory captures, multi-jumps that must carry on, a choice of
captures, crowning ending the move, kings, no move loses, the 80-ply draw; Reversi's flips, passes and the end;
Dots and Boxes' sizes, numbering, extra turns and two boxes at once; Sea Battle's placing checks, shots, sinking,
the view never holding the other fleet), the computer stand-ins, and the **JavaScript and Python rules agreeing**
on 400 random games each (2,400; `tests/js/turns-fuzz.js` writes them). `tests/test_turns.py` — invites (7 days,
several, not in the way, validation, children), the start and first mover, moves out of turn / illegal / stale /
strangers / after the end, a whole game with scores, play time and head to head, Practice, children's time and
quiet hours on moves, resigning, 7 days without a move, no result, Sea Battle through the API (the other fleet
never sent until the end), notifications with the fake Home Assistant (once every 15 minutes, owed ones sent by
housekeeping, opening the match settles it, quiet hours, the switches, the first move after the start), the 2–4
player plumbing with a dummy dice game (up to three others, starting when all joined or with those who have,
declines, server dice that can't be chosen or repeated and are stored, a leaver played by the computer, a timeout
with more players) and migration 8. `tests/js/wave7.test.js` — the registry, first movers, the computer's moves
legal and deterministic at every level, the levels' strength, scores, each game's rules and taps, one screen's
pass-the-phone, turn by turn (the board from the server's moves or Sea Battle's view, a move sent not played,
syncing, an end decided by the server), saving, a fuzz run, no engine-dependent maths, every mode drawn in all six
looks and a match from a picture through the game file.

**Built (wave 8):** `tests/test_wave8.py` — the game table and limits; Ludo's rules (six to enter, exact home,
captures and safe squares, own tokens sharing, another roll on a 6, the third 6 lost, passes, the winner and places,
2/3/4 colours, `forced`), Snakes and Ladders' (both boards: ladders and snakes never chained or crossing the ends,
any/exact finish), Chess's (castling through and out of check, en passant only right after, promotion needing a
choice, checkmate, stalemate, threefold, fifty moves, insufficient material, and **perft**: the start position to
depth 4, "Kiwipete" to 3, positions 3 to 4, 4 and 5 to 3), the computer stand-ins; the **JavaScript and Python rules
agreeing** on 400 Ludo, 400 Snakes and Ladders and 160 Chess random games (`tests/js/wave8-fuzz.js`); through the
API: 3- and 4-player Ludo and Snakes and Ladders matches (start with those joined, the server's dice the same on
asking again and never from the phone, forced moves played at the roll, a leaver played by the computer to the end,
scores), a whole Chess match with castling, en passant and promotion, and Carrom's live room (shots relayed to both
in order, only from the mover, numbering, late shots after the timer, the 30 s weak shot, pause stopping the clock,
checksums, shots stored, results). `tests/js/wave8.test.js` — the four games register; DiceKit (one phone, pass and
play with the computer filling seats, the picture from the server, syncing, the roll sent); Ludo, Snakes and Ladders,
Chess (perft in JavaScript, the engine's moves legal and the same for a position at every level, Hard beating Easy,
the worker answering the same move) and Carrom (fixed-point physics without engine-dependent maths, the same
checksum for the same shots on two instances over many random shots, fouls, the queen and its cover, the 300-shot
end, the computer's levels, two phones taking turns through a fake relay with delays and a timer shot, the turned-
round view); every mode in all six looks. `tests/js/backnav.test.js` and the repository's
`tests/test_common_static.py` (`BackNavJs`) — a link or notification changing the address isn't Back; Back still is.

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
  can't choose or repeat, and a player who leaves taken over by the computer
  (*all built with wave 8, above*).
- Packaging: `wsproto` in requirements; no outside service is contacted.

## Planned games

Every game is a module in `static/games/` behind the same start screen, pause,
scores and limits; adding one means adding its files, one registry entry, one
entry in `games.py`, its level list (`level_kinds/<game>.py`, §11.9) and a line
in this file. Waves 1–8 are done: every planned game is built (Solitaire was in wave 4 and has been
removed). Every one of them can be played together from two phones: a race (§13.3) for waves 1–6 (except the
two-player games), turn by turn (§13.5) for wave 7, Chess, Ludo and Snakes and Ladders, and a live match
(§13.4) for Snake Duel, Paddle Duel, Tank Battle and Carrom (taking turns). The waves, in the order built:

4. **Sudoku** (Easy to Expert, notes, number lines, hints), **Word Guess**,
   **Word Search**, and the **daily challenge** (below). Sudoku's number lines are a per-person start-screen
   choice (`users.game_prefs`): rows, columns and boxes (default), rows and columns only, or none — a display
   choice only, outside the game's state, scores, races, daily challenges and saves (spec/GAMES.md "Sudoku:
   number lines").
5. **Bubble Pop**, **Gem Swap**, **Tower Stack**, **Runner**, **Lander**,
   **City Defense** — **built**: each with a level list played by one mode
   (§11.9), a gentle mode for small children (Bubble Pop *Relaxed*, Tower Stack,
   Runner, Lander and City Defense *Easy*; Gem Swap *Zen* has no clock), saving,
   all six looks, and a race (§13.3). Not in the daily challenge pool. Rules and
   modes: spec/GAMES.md.
6. **Slide Puzzle**, **Lights Out**, **Picture Logic**, **Tile Match**,
   **Code Breaker**, **Type Rain** — **built**: the four puzzles and Code Breaker
   are made from the seed (no level list; solvable or one-answer boards, checked
   by the tests), Type Rain has a stage list (§11.9); each with a gentle mode for
   small children (Slide Puzzle *Picture 3 × 3*, Lights Out *Little 3 × 3*,
   Picture Logic *5 × 5*, Tile Match *Little*, Code Breaker *Little*, Type Rain
   *Little ones*), saving, all six looks, keyboard, touch and controller, and a
   race (§13.3). Not in the daily challenge pool. Rules and modes:
   spec/GAMES.md.
7. Turn-by-turn family games: **Four in a Row**, **Tic-tac-toe**, **Checkers**,
   **Reversi**, **Dots and Boxes**, **Sea Battle** — **built**: each against the computer (Easy (gentle), Medium,
   Hard; deterministic from the seed), two players on one screen (Sea Battle with a "pass the phone" screen), and
   turn by turn from two phones (§13.5); no levels, no race; saving (not the two-phone mode); all six looks;
   keyboard, touch and controller. Checkers is English draughts (§13.5, spec/GAMES.md). Rules and modes:
   spec/GAMES.md.
8. **Ludo** and **Snakes and Ladders** (2–4 players, on one phone or each on
   their own), **Carrom** (on one phone, or live from two phones), **Chess**
   (against the computer, or turn by turn) — **built**: Ludo and Snakes and Ladders against the computer (it fills
   the other seats), everyone on one phone (pass and play; the computer can fill the empty seats up to four) and turn
   by turn from 2–4 phones with the server's dice (§13.5); Snakes and Ladders' two original boards (Classic, Gentle)
   and Finish (reach 100 / exactly on 100). Carrom against the computer (Easy, Medium, Hard), two on one screen,
   Doubles (four, two teams, on one screen) and live from two phones taking turns (§13.4) with whole-number physics
   and a checksum after every shot. Chess against the computer (Easy, Medium, Hard: a deterministic alpha-beta
   engine with node budgets, ≤ 1–2 s, in a Web Worker), two on one screen and turn by turn from two phones with the
   server checking every move. No levels, no race; saving (not the two-phone modes); all six looks; keyboard, touch
   and controller. Rules: spec/GAMES.md.

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
