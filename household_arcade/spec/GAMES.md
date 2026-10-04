# Household Arcade — the game files

The games run entirely in the browser. Each one is plain JavaScript drawing on a `<canvas>`, with no
outside libraries, fonts, images or sound files, and nothing that needs `eval` (it works under the
app's strict Content-Security-Policy). This page describes the files in `app/static/games/` and the
contract between them and the app's shell (`index.html` / `app.js`).

## Files

Loaded as classic scripts in this order, each with `?v=<app version>`:

| File | What it is |
|---|---|
| `kit.js` | `window.ArcadeKit`: the six looks, the renderer, the fixed-step loop and the shared game session (start, pause, scores, end result) |
| `sound.js` | `window.ArcadeSound`: short Web Audio effects, off by default |
| `registry.js` | `window.ArcadeGames`: `register(def)`, `get(id)`, `list()` |
| `snake-logic.js` | Snake rules — pure, no DOM, deterministic for a seed (`self.SnakeLogic`, or `module.exports` in Node) |
| `snake.js` | Snake input and drawing; registers `snake` |
| `brick-logic.js` | Brick Breaker rules — pure, no DOM, deterministic for a seed (`self.BrickLogic` / `module.exports`) |
| `brick.js` | Brick Breaker input and drawing; registers `brick` |
| `blocks-logic.js`, `blocks.js` | Falling Blocks rules (`BlocksLogic`) and drawing; registers `blocks` |
| `duel-logic.js`, `duel.js` | Paddle Duel rules (`DuelLogic`) and drawing; registers `duel` |
| `racer-logic.js`, `racer.js` | Lane Racer rules (`RacerLogic`) and drawing; registers `racer` |
| `flap-logic.js`, `flap.js` | Flap rules (`FlapLogic`) and drawing; registers `flap` |
| `mines-logic.js`, `mines.js` | Mines rules (`MinesLogic`) and drawing; registers `mines` |
| `merge-logic.js`, `merge.js` | Merge rules (`MergeLogic`) and drawing; registers `merge` |
| `colours-logic.js`, `colours.js` | Colour Memory rules (`ColoursLogic`) and drawing; registers `colours` |
| `cards-logic.js`, `cards.js` | Memory Cards rules (`CardsLogic`) and drawing; registers `cards` |
| `mole-logic.js`, `mole.js` | Tap the Mole rules (`MoleLogic`) and drawing; registers `mole` |
| `numbers-logic.js`, `numbers.js` | Number Dash rules (`NumbersLogic`) and drawing; registers `numbers` |
| `tanks-logic.js`, `tanks.js` | Tank Battle rules (`TanksLogic`) and drawing; registers `tanks` |
| `invaders-logic.js`, `invaders.js` | Sky Defenders rules (`InvadersLogic`) and drawing; registers `invaders` |
| `rocks-logic.js`, `rocks.js` | Rocks rules (`RocksLogic`) and drawing; registers `rocks` |
| `hop-logic.js`, `hop.js` | Road Hop rules (`HopLogic`) and drawing; registers `hop` |
| `snakeduel-logic.js`, `snakeduel.js` | Snake Duel rules (`SnakeDuelLogic`) and drawing; registers `snakeduel` |
| `sudoku-logic.js`, `sudoku.js` | Sudoku rules (`SudokuLogic`; seeded puzzles with one solution, notes, hints) and drawing; registers `sudoku` |
| `solitaire-logic.js`, `solitaire.js` | Klondike rules (`SolitaireLogic`; a solver makes every deal winnable) and drawing; registers `solitaire` |
| `wordguess-words.js`, `wordguess-logic.js`, `wordguess.js` | Word Guess word lists, rules (`WordGuessLogic`) and drawing (a keyboard on the canvas); registers `wordguess` |
| `wordsearch-words.js`, `wordsearch-logic.js`, `wordsearch.js` | Word Search word lists, grid maker (`WordSearchLogic`) and drawing; registers `wordsearch` |

## The contract with the shell

**Game definition** (from `ArcadeGames.get(id)` / `list()`):
`{ id, name, modes: [{ id, label }], defaultMode, controls: "dpad" | "paddle" | "buttons" | "touch", buttons, padLabel, players, tap, help, stateVersion, create(canvas, opts) }`

- `dpad`: the shell draws an arrow pad and turns swipes on the game into `up`/`down`/`left`/`right`; with `tap`
  set, a touch that doesn't swipe sends that action (Road Hop: `up`).
- `paddle`: a drag strip and one button (`padLabel`, default "Launch") that sends `fire`; the game gets the canvas
  pointer (and mouse moves without a button).
- `buttons`: the shell draws the game's `buttons` (`[{ action, label, aria, wide, place }]`, held while pressed)
  — one wrapping row, or a grid when every button has `place: [col, row, colSpan, rowSpan]` — and the game gets
  the canvas pointer (`down` / `move` while pressed / `up`).
- `touch`: like `buttons`, but the buttons are optional (the game is played on the canvas itself).
- Actions: `up`, `down`, `left`, `right`, `fire`, `alt` (C, F or Shift; a controller's X/Y) and, for
  `players: 2`, `up2`, `down2`, `left2`, `right2`, `fire2`: then the arrows, Space and Enter are player 1 and
  W A S D, Q and E player 2.

| Game | id | Modes (id — label) | Default | Controls |
|---|---|---|---|---|
| Snake | `snake` | `walls-slow` Walls · Slow, `walls-normal` Walls · Normal, `walls-fast` Walls · Fast, `wrap-slow` Wrap · Slow, `wrap-normal` Wrap · Normal, `wrap-fast` Wrap · Fast, `maze` Maze | `walls-normal` | `dpad` |
| Brick Breaker | `brick` | `powerups` Power-ups, `classic` Classic | `powerups` | `paddle` (Launch) |
| Falling Blocks | `blocks` | `classic` Classic, `fast` Fast start, `rising` Rising floor, `challenge` Challenges | `classic` | `buttons` Hold ◀ ↻ ▶ ▼ Drop |
| Paddle Duel | `duel` | `easy` Easy, `normal` Normal, `hard` Hard | `normal` | `paddle` (Serve) |
| Lane Racer | `racer` | `three` 3 lanes, `four` 4 lanes, `rush` Rush (one life), `stages` Stages | `three` | `buttons` ◀ ▶ |
| Flap | `flap` | `easy` Easy, `normal` Normal, `moving` Moving gates, `course` Courses | `normal` | `buttons` Flap |
| Mines | `mines` | `easy` Easy, `medium` Medium, `hard` Hard, `boards` Shaped boards | `easy` | `touch` 🚩 Flag |
| Merge | `merge` | `classic` 4 × 4, `big` 5 × 5, `small` 3 × 3 (hard), `goals` Goals | `classic` | `dpad` |
| Colour Memory | `colours` | `classic` Classic, `fast` Fast, `reverse` Backwards, `challenge` Challenges | `classic` | `touch` |
| Memory Cards | `cards` | `small` 4 × 4, `medium` 4 × 5, `large` 5 × 6, `challenge` Challenges | `small` | `touch` |
| Tap the Mole | `mole` | `classic` Classic, `big` Big garden, `rush` 60 seconds, `gardens` Gardens | `classic` | `touch` |
| Number Dash | `numbers` | `add` Add & take away, `times` Times tables, `mixed` Mixed, `challenge` Challenges | `add` | `touch` |
| Tank Battle | `tanks` | `classic` Classic, `easy` Easy | `classic` | `buttons` ◀ ▲ ▼ ▶ Fire |
| Sky Defenders | `invaders` | `classic` Classic, `easy` Easy, `waves` Waves | `classic` | `buttons` ◀ ▶ Fire |
| Rocks | `rocks` | `classic` Classic, `calm` Calm, `waves` Waves | `classic` | `buttons` ⟲ ⟳ ▲ Fire |
| Road Hop | `hop` | `classic` Classic, `easy` Easy, `levels` Levels | `classic` | `dpad`, tap = `up` |
| Sudoku | `sudoku` | `easy`, `medium`, `hard`, `expert` | `easy` | `buttons` number pad (5-column `place` grid), `typed` |
| Solitaire | `solitaire` | `draw1` Draw one, `draw3` Draw three | `draw1` | `touch` Hint, Auto, Undo |
| Word Guess | `wordguess` | `classic` Six tries, `easy` Eight tries, `strict` Strict | `classic` | `touch`, `typed` |
| Word Search | `wordsearch` | `little`, `kids`, `family`, `puzzler` | `kids` | `touch` Hint |
| Snake Duel | `snakeduel` | `cpu` Against the computer, `two` Two players | `cpu` | `touch`, two players |

The shell hides `powerups` when the App setting "Brick Breaker power-ups" is off.

**Racing on two phones** (spec/SPEC.md §13.3): a definition's `race` field (default `true`, `false` for `players: 2` and
for a game that opts out) says the game can be played in a race: both phones create the game with the same
`opts.seed`, `opts.mode` and `opts.levels` (so a game's randomness must come only from the seed), each sends its
`status()` a few times a second and its final result like any game. Which games the server offers a race for, and
how the winner is decided, is the game's `race` entry in `app/games.py`.

**`create(canvas, opts)`** — `opts = { mode, look, sound, reduceMotion, handedness, seed, onScore(score, level), onEnd(result), onEvent(type, data) }`.
All are optional. `look` is a look id (unknown → `modern`); `seed` makes a game repeatable (default: random);
`handedness` is accepted and not used by the games (the on-screen controls are the shell's).
One optional extra: `best` (a number) shows "BEST n" in the Snake and Flap HUD instead of the level.

**The instance**:

| Member | Does |
|---|---|
| `start()` | Starts the game (a first frame is already drawn by `create`). After the game is over, `start()` plays a new game. While paused it resumes. |
| `status()` | `{ score, level, over, paused }` right now (for playing together: the race bar shows the other player's; any game built on `ArcadeKit.createSession` has it, nothing to add in the game) |
| `pause()`, `resume()`, `paused` | Pause stops the animation loop; no game time passes. |
| `setLook(id)`, `setSound(on)`, `setReduceMotion(on)` | Apply at once, also mid-game. `setSound` also calls `ArcadeSound.setEnabled`. |
| `input(action, isDown)` | `up`, `down`, `left`, `right`, `fire`, `pause` (each game's use: the tables above and below). Snake turns on key-down. Brick Breaker: `left`/`right` held move the paddle, `fire` (or `up`) launches. `pause` toggles pause. Ignored unless the game is running. |
| `pointer(kind, x, y)` | `down` / `move` / `up`, x and y in CSS pixels relative to the canvas. Snake: a swipe of 18 px turns (the page itself turns after 14 px) (keep swiping for more turns). Brick Breaker: the paddle follows x from anywhere on the canvas (the finger needn't cover the paddle); `up` launches a waiting ball. Mouse `move` without a button also moves the paddle (Paddle Duel too; `up` serves). Falling Blocks: drag, tap, flick (above). Lane Racer: `down` on the left / right half changes lane. Flap: `down` flaps. |
| `resize()` | Call after the canvas's CSS size changes (a ResizeObserver also does this). |
| `destroy()` | Stops everything and frees the off-screen canvases. |
| also | `state` (`ready`, `running`, `paused`, `over`, `destroyed`), `seconds`, `score`, `level`, `renderer`, `logic` (the rules state, read-only, for tests). |

**Callbacks**: `onScore(score, level)` whenever either changes (and `0, 1` at start).
`onEnd({ score, level, seconds, stats })` once, when the game ends; `seconds` is whole seconds of
active play (pauses excluded), counted from the fixed updates. `onEvent(type, data)`:
`start`; `pause` with `{ paused: true | false }` on every pause change, including the game pausing
itself when the page is hidden; Snake `eat`, `bonus`, `bonusSpawn`, `bonusGone`, `speed`, `die`, `win`;
Brick Breaker `launch`, `paddle`, `wall`, `hit`, `break`, `drop`, `powerup`, `powerEnd`, `lifeLost`,
`level`, `gameover`; Falling Blocks `move`, `turn`, `hold`, `drop`, `lock`, `row`, `level`, `rise`, `win`, `gameover`;
Paddle Duel `launch`, `paddle`, `hit`, `wall`, `point`, `miss`, `level`, `win`, `gameover`; Lane Racer `turn`,
`coin`, `crash`, `level`, `win`, `gameover`; Flap `flap`, `gate`, `level`, `win`, `gameover`; Mines `open`, `flag`,
`unflag`, `mode`, `cursor`, `clear`, `level`, `boom`, `win`, `gameover`; Merge `move`, `merge`, `goal`, `level`,
`win`, `gameover`; Colour Memory `toneUp`, `toneLeft`, `toneRight`, `toneDown`, `yourTurn`, `round`, `level`,
`win`, `gameover`; Memory Cards `turn`, `cursor`, `pair`, `miss`, `back`, `hide`, `clear`, `level`, `win`,
`gameover`; Tap the Mole `pop`, `bonk`, `gold`, `hog`, `whiff`, `miss`, `life`, `level`, `win`, `gameover`;
Number Dash `question`, `right`, `wrong`, `streak`, `level`, `win`, `gameover`; Tank Battle `spawn`, `fire`,
`brick`, `steel`, `hit`, `lifeLost`, `clear`, `level`, `win`, `gameover`; Sky Defenders `fire`, `hit`, `bonus`,
`ship`, `zap`, `chip`, `lifeLost`, `level`, `win`, `gameover`; Rocks `fire`, `break`, `saucer`, `saucerHit`,
`lifeLost`, `clear`, `level`, `win`, `gameover`; Road Hop `hop`, `bump`, `land`, `home`, `splat`, `level`, `win`,
`gameover`; Snake Duel `go`, `eat`, `crash`, `round`, `match`, `level`, `win`, `gameover`. A callback that throws
is logged and doesn't stop the game.

`stats` — Snake: `length, foods, bonus, won, cause ("wall" | "self" | null), mode, moves`.
Brick Breaker: `bricks, levelsCleared, powerups, paddleHits, livesLeft, mode`.
Falling Blocks: `lines, pieces, singles, doubles, triples, fours, holds, rowsRisen, challenges, won, cause, mode`.
Paddle Duel: `won, matches, pointsWon, pointsLost, returns, longestRally, cause, mode`.
Lane Racer: `distance, rows, coins, crashes, livesLeft, stages, won, cause, mode`.
Flap: `gates, flaps, courses, won, cause ("gate" | "ground" | "won"), mode`.
Mines: `boards, squares, flags, mines, won, cause, mode`. Merge: `moves, merges, bestTile, goals, won, cause, mode`.
Colour Memory: `rounds, longest, presses, challenges, won, cause ("wrong" | "time" | "won"), mode`.
Memory Cards: `boards, pairs, misses, bestStreak, turns, won, cause ("time" | "won"), mode`.
Tap the Mole: `bonks, golden, missed, hedgehogs, livesLeft, gardens, won, cause, mode`.
Number Dash: `right, wrong, bestStreak, challenges, won, cause, mode`.
Tank Battle: `kills, shots, arenas, bricks, livesLeft, livesLost, won, cause ("lives" | "flag" | "won"), flagBy, mode`.
Sky Defenders: `critters, ships, shots, waves, livesLeft, won, cause ("bombed" | "landed" | "won"), mode`.
Rocks: `rocks, shots, saucers, waves, livesLeft, livesLost, won, cause, mode`.
Road Hop: `hops, homes, levels, deaths, livesLeft, won, cause ("car" | "water" | "swept" | "time" | "won"), mode`.
Snake Duel: `won, cause, foods, rounds, roundsLost, draws, matches, matchesLost, arenas, winner, moves, mode`.

**Canvas**: the shell gives the canvas its CSS size (any shape); the game draws its 240 × 300 logical
playfield and HUD (score, level, lives / mode) centred in it, letterboxed in the look's background
colour, at the device pixel ratio (capped at 2; 1.5 for Neon). Start screen, pause and game-over
overlays, touch buttons and keyboard mapping are the shell's. The game listens only to
`visibilitychange` / `pagehide` (to pause itself) — never to the keyboard.

**Never in the background**: the loop runs on `requestAnimationFrame` only while the game is running;
it stops on pause, game over, `destroy()` and when the page is hidden.

## How the games run

- **Fixed step**: 60 updates a second whatever the screen's refresh rate (30, 60, 120, 144 Hz),
  so speed and scores are the same on every device. A small tolerance keeps a 60 Hz screen at exactly
  one update per frame; after a stall (tab switch) the game doesn't fast-forward. Drawing interpolates
  between updates (paddle, ball, the snake's head and tail), so movement is smooth on fast screens.
- **Snake** (`snake-logic.js`): 20 × 20 grid, starts 4 long heading right. Speeds 6 / 8 / 11 cells a
  second (counted exactly in whole steps, no drift), +0.6 every 5 foods (that's also the "level"),
  capped at 1.8 × the start speed and 16. Turn queue of up to 3: a turn into the same or opposite
  direction of the last queued turn is ignored, so two quick turns within one move both happen. A
  turn pressed when the snake is past half-way into its next cell is made at once (that move comes
  early) so turns feel immediate at every speed. Food
  10 × multiplier (1 / 1.5 / 2); after the third food each food has a 1-in-4 chance of a bonus star
  worth 50 that lasts 5 seconds (it blinks for its last 1.5 s, twice a second; with reduce motion it
  shows a shrinking ring instead). Moving into the cell the tail is leaving is allowed. Filling the
  board wins.
- **Brick Breaker** (`brick-logic.js`): 8 bricks wide; ten original layouts (First wall, Staircase,
  Diamond, Checkers, Gate, Waves, Heart, Pillars, Target, Finale), then they repeat 15 % faster each
  round. Ball 156 px/s at level 1, +4 % a level, a little faster with each paddle hit until a ball
  is lost, capped. The ball's angle comes only from where it hits the paddle: straight up in the
  middle, up to 60° at the edges. It moves in sub-steps of at most 2 px, so it never passes through a
  brick. Bricks take 1–3 hits and score 10 / 20 / 30 when broken; clearing a level scores 100 × level.
  Three lives (up to five). Power-ups (Power-ups mode only; 14 % of broken bricks drop one): **W** wider
  paddle for 15 s, **S** slower ball for 10 s (timers shown at the bottom right), **B** extra ball (until
  it falls), **+** extra life. The paddle follows a finger or mouse closely (75 % of the distance per
  update, at most 30 px), and the arrow keys bring it to full speed in about three updates.
- **Falling Blocks** (`blocks-logic.js`): a 10 × 20 well plus two hidden rows where pieces appear; the seven
  four-square shapes from a shuffled bag (all seven in every seven pieces); next three shown, and the landing
  place as an outline. Gravity 48 updates a row at level 1 down to 2 at level 20 (the top); a level every 10 rows
  (*Fast start* begins at 6). ← → move (held: repeat after 10 updates, then every 3), ↑ turns clockwise (with
  small wall kicks), ↓ held drops a row every 2 updates (1 point a row), Space drops at once (2 a row). A piece on
  the floor locks after 30 updates; moving or turning restarts that up to 15 times. Rows 1–4 at once score
  100 / 300 / 500 / 800 × level; full rows fade out over 20 updates (no flashing), and the next piece comes 10
  updates later. *Rising floor*: every 12 s (0.5 s sooner a level, at least 5 s) a grey row with one gap pushes
  up from the bottom. **Hold** (`alt`: C, Shift, the Hold button): put the falling piece aside, or swap it with the
  one put aside before; once per piece; shown under the next pieces. *Challenges*: each challenge starts a fresh
  well with its starting rows after a 2.5 s pause; clear its `goal` rows (scored × its `speed`, not the level)
  to move on; Hold and the next pieces carry over. The game ends when a new piece has no room or a piece locks
  wholly above the well. On a phone the buttons are Hold | ◀ ↻ ▶ with ▼ under ↻ | Drop. Touch:
  drag sideways moves a column per 12 logical px, a tap turns, dragging down drops a row per 12 px, a quick flick
  down drops.
- **Paddle Duel** (`duel-logic.js`): your paddle at the bottom (48 wide), the computer's at the top (44). The ball
  starts at 2.4 px an update (+0.12 per match), gains 0.12 each hit up to 6, and leaves a paddle at up to 55°
  from where it hit; sub-steps of at most 2 px. Serve after 1 s (Space or a tap serves at once), toward whoever
  lost the point. The computer moves at 1.7 px an update (+0.24 a match, Easy −0.5, Hard +0.5, at most 4.4) to
  where it predicts the ball will meet it, choosing each time an offset on its paddle (nearer the edge against
  better opponents) or, with a chance of 30 % at match 1 falling to 6 % (Easy +10 %, Hard −6 %, +10 % against
  your edge shots), just off it. Every mode plays the opponent list (built in: the ten above; each sets the
  computer's speed, miss chance, aim, the serve speed and the points to win). Return 10; point 100 × min(match,
  10); winning a match +1,000 × min(match, 10) and the next opponent comes on; losing a match ends the game;
  beating the last opponent wins it.
- **Lane Racer** (`racer-logic.js`): road 180 px wide in 3 or 4 lanes; a lane change takes 10 updates. Speed 3 px
  an update (*Rush* 5) +0.25 a level, at most 7.5; a level every 2,500 px of road, at most 40. Traffic comes in
  rows `TRUCK_H + 30 + 16 × speed` px apart (room for a lane change and more); each row blocks 1 to lanes − 1
  lanes, keeps one lane open within one change of the last row's, and keeps every lane that was open in the last
  row within one change of an open lane — so whichever way through you took, there's a way on (a test drives
  10 minutes at every speed without a crash). One car in five is a truck. 45 % of rows have a coin (25) in the
  gap before the next. 1 point per 10 px. Three lives (*Rush* one); a crash clears the traffic near you and
  gives 1.5 s without crashes (the car blinks 2.5 times a second; with reduce motion a ring instead). *Stages*:
  each stage sets lanes, speed, the rows of traffic to a checkered finish line and how much traffic, trucks and
  coins (the rows still always leave a way through); 3 lives for the whole run; ~1.5 s "STAGE CLEAR" between.
- **Flap** (`flap-logic.js`): waits for the first flap. Gravity 0.15, a flap sets the speed to −2.7 (one flap
  lifts about 24 px), falling at most 3.8 px an update; the top of the play area stops the flyer, the ground ends
  the game. Gates 34 wide, 124 px apart, a gap's middle at most 56 px from the last one; gaps 86 px (Easy 100, Moving 90) narrowing 3 px a
  level to at least 68 (82, 72); 1.6 px an update (Easy 1.4) +0.08 a level up to 2.6. One point a gate, a level
  every 10. *Moving gates* sway ±16 px. *Courses*: each course sets its gates, gap, speed, sway, spacing and a
  pattern of heights (digits 0 high – 9 low, used in turn, neighbours at most 4 apart); the end of a course is
  the next level. A continued saved game waits for a flap.
- **Mines** (`mines-logic.js`): Easy 9 × 9 with 10 mines (24 px squares), Medium 10 × 12 with 18 (20 px), Hard
  12 × 15 with 32 (16 px). Mines are laid at the first open, which with its neighbours is always clear; an empty
  square opens its neighbours; opening a number whose flags match opens the rest. Flag: hold a square 24 updates
  (a ring fills), or tap with 🚩 Flag mode on, or `alt` on the keyboard cursor. 1 a safe square; a cleared board
  10 × mines + up to 5 × mines for speed; the next board (+2 mines, Hard +3, capped) comes 90 updates later.
  Opening a mine ends the game. *Shaped boards*: picture shapes (6–15 × 6–12, ≥ 30 squares in one piece) with
  8–22 % mines.
- **Merge** (`merge-logic.js`): 4 × 4, 5 × 5 or 3 × 3; a move slides every tile as far as it goes, equal tiles
  that meet merge once a move and score the new value; a 2 (90 %) or 4 appears after a move that changed the
  board; moves at least 6 updates apart (one press during a slide is kept). Level = the power of two of the
  largest tile; 2048 sends `win` once and play goes on; no move left ends it. *Goals*: a board (3–5, up to 3
  fixed stones that block sliding) and a tile to make; making it brings the next board.
- **Colour Memory** (`colours-logic.js`): four pads in a diamond (up red, left green, right blue, down yellow,
  each with its arrow). Each round plays the sequence plus one new pad, then you repeat it (*Backwards*: last to
  first). Lights and gaps never shorter than 10 updates (at most 3 a second). A wrong pad or 5 s without a press
  ends the game. A round scores 10 × its length. *Challenges*: grow the sequence to `length` to clear it; each
  sets its light and gap times, direction and starting length.
- **Memory Cards** (`cards-logic.js`): 4 × 4, 4 × 5 or 5 × 6 cards with 15 original shape symbols. A card turns
  in 6 updates; a pair stays up and scores 20 × the pairs-in-a-row streak; a miss turns back after 45 updates.
  A time bar per board (60 / 75 / 110 s, 2 s less a board); clearing scores 5 a second left + 10 × (pairs −
  misses) and deals the next board; time out ends the game. *Challenges*: each board sets its size, time, a peek
  at every face before play and how many kinds of symbol.
- **Tap the Mole** (`mole-logic.js`): 3 × 3 holes (*Big garden* 4 × 4). Moles rise for 8 updates, stay up 120
  (−8 a level, at least 36), a new critter at least 24 updates after the last; bonk 10, a golden mole 50 (at
  most one every 180 updates). From level 3 hedgehogs: tapping one costs a life (in *60 seconds*, 5 s). A level
  every 10 bonks. Three lives; a mole that gets away costs one. *Gardens*: shapes of holes with their own bonks
  to clear, timings and shares of hedgehogs and golden moles.
- **Number Dash** (`numbers-logic.js`): a sum and four answers in a diamond (↑ ← → ↓). A 60 s clock: right +1 s,
  wrong −3 s (and the right answer shown); a new sum 12 updates after a right answer, 45 after a wrong one.
  10 + 2 × level (level capped at 25 for points), ×2 from 5 in a row, ×3 from 10; a level every 8 right answers.
  *Challenges*: which sums (+ − × ÷), how big, right answers to clear and the seconds on a fresh clock.
- **Tank Battle** (`tanks-logic.js`): a 13 × 13 arena of 18 px squares; your tank and the flag at the bottom;
  enemies enter at the top (at most one every 90 updates, four at once). One shell each in the air; brick breaks
  a strip at a time, steel stops shells, water stops tanks, bushes hide them. 100 a tank, 500 an arena. A hit
  costs a life (3, Easy 5) and you come back with a 2.5 s shield; a shell on the flag ends the game. Every mode
  plays the arena list (map, enemies, speed, fire). Your tank drives 1.5 px an update and its shell flies 4.5.
  Controls are made to feel immediate: a direction pressed and let go between two updates still turns and moves
  the tank once (`tapDir`); Fire pressed while your shell is still flying is kept for 12 updates and fires as soon
  as it's gone; driving into a corner you're up to 5 px off slides you into the gap instead of stopping.
- **Sky Defenders** (`invaders-logic.js`): a formation of original critters (30 / 20 / 10) marches and steps
  down, quicker as fewer are left, and drops bombs; one shot at a time (≥ 15 updates apart); up to 4 shields
  that wear away; a bonus ship (50–300) at most every 20 s. 3 lives; the formation reaching the ground ends the
  game. *Waves*: the formation (3–6 rows of 11), speed, bombs and shields.
- **Rocks** (`rocks-logic.js`): a ship in wrap-around space; turn, thrust, fire (4 shots at most, ≥ 10 updates
  apart). Big rocks (20) split into two medium (50), medium into two small (100); a saucer (300) from wave 3.
  3 lives (Calm 5) with a safe moment after each. *Waves*: big and medium rocks, speed, saucer chance.
- **Road Hop** (`hop-logic.js`): 13 columns of 18 px; a hop takes 8 updates (Space or a tap hops forward). Cross
  the road (cars squash) and the river (ride logs and shell rafts; open water and riding off the edge lose a
  hopper) to 5 homes before the time runs out: 10 a new row, a home 50 + seconds left, all five +250 and the
  next level. 3 lives. *Levels*: the lanes (kind, pattern, speed, direction) and the time, checked crossable.
- **Snake Duel** (`snakeduel-logic.js`): two snakes on 24 × 20; player 1 green (right side, arrows), player 2 blue
  (left side, W A S D, or the computer). Crashing into a wall, yourself or the other snake loses the round; heads
  meeting is a draw; first to 3 round wins takes the match (rounds at most 60 s, matches at most 9 rounds). Player
  1's score: food 10, round 100, match 500 × min(arena, 10). Against the computer the arenas come in order and
  losing a match ends the game; two players play one match. On a phone, swipes on the right half steer green and
  on the left half blue. Both modes play the arena list (walls, speed, foods).
- All seventeen stay inside the server's honest-score limits during play, also with the hardest levels their
  lists allow (checked by the tests with quick bots).

## Looks and drawing

`ArcadeKit.LOOKS` has `modern`, `lcd`, `neon`, `pixel`, `paper`, `contrast`, each with a `label`.
The palettes and shapes are those of the approved previews. A game draws through `g`
(`rect`, `circle`, `cell`, `line`, `text`, `star`, `board`, `hud`, `layer`, …) and the look decides
how that appears; looks never change the rules.

- **Modern** follows the app's light/dark theme (the page's `color-scheme`, else the system setting;
  it updates when the theme changes).
- **Retro LCD** is drawn on a 160 × 200 grid, then turned into dark dots on a grey-green screen with
  `ImageData` once a frame: ink where the scene is dark, a faint ghost of the unlit dot elsewhere. The
  canvas backing store is sized so each dot is a whole number of pixels, and a cached grid layer draws
  the gaps. Snake cells sit exactly on the grid (7 dots); blocks are drawn handheld-style
  (outline + centre).
- **Pixel** is drawn on a 120 × 150 canvas and scaled up without smoothing.
- **Neon** draws outlines on black, then adds a glow made by halving the finished picture a few times
  and adding the blurred copy back once (no `shadowBlur`); the glow pulses gently unless reduce
  motion is on. The ball leaves a short trail.
- **Paper** has ruled lines (a cached layer) and doubled pencil strokes; **High contrast** uses
  black, white and strong colours with thick outlines, no glow or texture.
- Static parts are cached: the background, and a level's bricks (redrawn only when a brick is hit).
- Measured with the Playwright harness at 390 px and desktop size in every look: 60 fps with no long
  frames; frame cost in headless Chromium about 0.4–1.5 ms (Neon about 3.5 ms).

**Fonts**: no font files are shipped or downloaded. Modern and High contrast use
`Atkinson Hyperlegible` if installed, else the system UI font; Neon `Bungee`, else Arial Black /
system bold; Paper `Patrick Hand`, else Comic Neue / Comic Sans MS / Chalkboard SE / Segoe Print /
cursive. Retro LCD and Pixel draw all their text with a small built-in pixel font (original 5-row
glyphs in `kit.js`), so they look the same everywhere.

**Reduce motion** (the person's setting or the browser's `prefers-reduced-motion`) turns off the Neon
glow pulse and trail, screen shake when a life is lost, brick particles and the bonus blink. Nothing
flashes faster than 3 times a second. Brick toughness is shown by stripes (one per extra hit still
needed; Retro LCD: hollow / outlined / solid), not only by colour.

## Sound

`ArcadeSound.setEnabled(bool)`, `ArcadeSound.play(name, lookId?)`, `isEnabled()`. Off by default;
while off nothing plays and no `AudioContext` exists. The context is created during a user gesture
(or once the page has had one) and resumed on the next gesture if the browser suspended it. Effects:
`eat`, `bonus`, `speed`, `bounce`, `wall`, `hit`, `break`, `powerup`, `launch`, `lose`, `level`, `row`,
`win`, `gameover` (and `turn`). Each look has its own sound set: Retro LCD and Pixel square-wave
beeps, Neon a filtered sawtooth, Paper soft sine, Modern and High contrast triangle.

## Levels from data (SPEC §11)

Brick Breaker and Snake's Maze mode take `opts.levels`: an array of that game's level data, numbered from 1,
from the session. Without it (or with nothing usable in it) they play their built-in list (`BrickLogic.LAYOUTS`,
`SnakeLogic.MAZES`). Brick Breaker's level is `{ name, rows }` as in `LAYOUTS`; a maze is
`{ name, foods, walls: [20 strings of "." and "#"] }`. The rules files check the shape again
(`BrickLogic.usableLevels`, `SnakeLogic.usableMazes`) and skip a level that doesn't fit.

Snake · Maze: the snake starts on row 10 at columns 6–9 heading right, at Normal speed plus 0.3 cells a second
per maze; walls end the game like the board's edge; food never lands on a wall; eating `foods` pieces clears the
maze (`level` event, 50 × its number) and the next one starts with the snake back at the start; the last one
ends the game as won (`win`). The HUD shows "MAZE n/total" and the maze's name and progress at the bottom; walls
are a cached layer.

Every other game takes `opts.levels` in the modes that play its list (SPEC §11.9) and has `LEVELS` (its built-in
list, the same as `level_kinds/<game>.py`) and `usableLevels(levels)`. Level n is the list's n-th entry; finishing
it starts the next (`level` event), finishing the last sends `win` and ends the game won (`stats.won`). The list
is not part of a save; `restore(data, levels)` takes it back. The HUD shows the list's noun and "n/total" and the
level's name as it starts.

## Saving a game (SPEC §12)

A game that can be saved registers `stateVersion` (its save format) and gives the kit two more `impl`
functions: `save()` → its rules state as plain data (no level list: the server keeps that), and
`restore(data)` → carry on from it (using `opts.levels`). `Logic.save(s)` / `Logic.restore(data, levels)` do
it for every game (`STATE_VERSION` 1; `restore` throws on data that isn't a game). The instance
gets `canSave` and `save()` → `{ state, score, level, seconds }` (null unless running or paused); created with
`opts.restore = { state, seconds }` it starts from there and its `seconds` continue. A change to what a game's
state means is a new `STATE_VERSION` (and the same number in `games.py`), so older saves can only be ended.

## Wave 4 additions to the contract

- Definition fields `options` (start-screen choices stored per person, passed as `opts.options`; Sudoku: Show mistakes, Number lines) and `typed: true` (the shell sends physical keys as `input("key:A" | "key:ENTER" | "key:BACKSPACE", true)`, and P no longer pauses). A `preview` opt makes the still picture behind the start card.
- Daily play passes `opts.seed` and a mode from the day's challenge; none of the four have level lists, so no `level_kinds` entry (the puzzles are made from the seed).
- Results carry `stats.summary` and `stats.won`; scores are higher-is-better. `unfinished_zero` games score nothing until solved.

## Adding a game later

Add `<game>-logic.js` (pure rules with `create`, `step`, `result`) and `<game>.js`, which builds an
`impl` (`init(seed)`, `step()` → events, `input`, `pointer`, `draw(g, info)`, `score()`, `level()`,
`isOver()`, `result()`, `sounds`) and returns `ArcadeKit.createSession(canvas, opts, impl)` from its
`create`, then calls `ArcadeGames.register(...)`. Add the two script tags and the server's game-table
entry. Nothing else in the kit or the shell changes.

## Tests

`node --test tests/js` (or `node --test tests/js/*.test.js`) — Node 18 or newer, no packages:

- `snake-logic.test.js`: movement and speeds, turn queue, opposite turns, growth and scoring with the
  multiplier, walls vs wrap, self-collision and the tail rule, speed-up every 5 foods, bonus food,
  winning, determinism, honest-score limits.
- `brick-logic.test.js`: layouts, serving and launching, pointer and keyboard paddle, paddle angle,
  walls, brick toughness and points, lives and game over, level clear and the next layout, repeats
  getting faster, power-ups on/off and each power-up, extra balls, no tunnelling at top speed,
  determinism, honest-score limits.
- `<game>-logic.test.js` for each of the other games (including the four wave 4 games): the rules (moves, scoring, levels, modes, the end),
  the level-list mode (built-in or the session's list, a bad list falls back, the next level, the last one
  wins), determinism, save and restore, a render in all six looks, and a quick bot playing within the
  honest-score limits, also on the hardest levels the list allows (Lane Racer: never forced to crash; Paddle
  Duel: rallies end).
- `tests/test_level_kinds.py` (Python): every game has a list; each kind's built-ins pass its checks, are all
  different and equal the rules file's `LEVELS`; bad values are refused; the prompt names every field.
- `games.test.js`: the registry and the contract, every look rendering each game while it plays
  (also switching looks, reduce motion and size mid-game), dark Modern, pointer mapping, pause by
  input / hidden page / `pagehide`, 60 updates a second at 30–144 Hz, no fast-forward after a stall,
  end results, sound off by default, a throwing callback.

`tests/js/helpers.js` loads the browser files into a small fake DOM (recording canvas contexts and a
hand-driven `requestAnimationFrame`); `tests/js/index.js` lets `node --test tests/js` find the tests.
