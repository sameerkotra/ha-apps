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
| `wordguess-words.js`, `wordguess-logic.js`, `wordguess.js` | Word Guess word lists, rules (`WordGuessLogic`) and drawing (a keyboard on the canvas); registers `wordguess` |
| `wordsearch-words.js`, `wordsearch-logic.js`, `wordsearch.js` | Word Search word lists, grid maker (`WordSearchLogic`) and drawing; registers `wordsearch` |
| `bubbles-logic.js`, `bubbles.js` | Bubble Pop rules (`BubblesLogic`) and drawing; registers `bubbles` |
| `gems-logic.js`, `gems.js` | Gem Swap rules (`GemsLogic`) and drawing; registers `gems` |
| `stack-logic.js`, `stack.js` | Tower Stack rules (`StackLogic`) and drawing; registers `stack` |
| `runner-logic.js`, `runner.js` | Runner rules (`RunnerLogic`) and drawing; registers `runner` |
| `lander-logic.js`, `lander.js` | Lander rules (`LanderLogic`) and drawing; registers `lander` |
| `defense-logic.js`, `defense.js` | City Defense rules (`DefenseLogic`) and drawing; registers `defense` |
| `slide-logic.js`, `slide.js` | Slide Puzzle rules (`SlideLogic`; solvable shuffles from the seed) and drawing (numbers, or a picture drawn from shapes); registers `slide` |
| `lights-logic.js`, `lights.js` | Lights Out rules (`LightsLogic`; boards from the seed, the exact fewest presses) and drawing; registers `lights` |
| `nonogram-logic.js`, `nonogram.js` | Picture Logic rules (`NonogramLogic`; puzzles with one answer, checked by a line solver) and drawing; registers `nonogram` |
| `tiles-logic.js`, `tiles.js` | Tile Match rules (`TilesLogic`; clearable deals from the seed) and drawing (symbols drawn from shapes and numbers); registers `tiles` |
| `codebreak-logic.js`, `codebreak.js` | Code Breaker rules (`CodeBreakLogic`) and drawing (symbol keys on the canvas); registers `codebreak` |
| `typerain-words.js`, `typerain-logic.js`, `typerain.js` | Type Rain word list, rules (`TypeRainLogic`) and drawing (a keyboard on the canvas); registers `typerain` |
| `boardkit.js` | `window.BoardKit` (or `module.exports`): what wave 7's board games share — `BoardKit.game(R)` makes a game's Logic from its rules (modes, whose move, the computer's moves, the cursor, scores, saving, the turn-by-turn picture), `BoardKit.session(canvas, opts, spec)` the drawing around the board (players, whose move, the end, "pass the phone", sending a move) |
| `fourrow-logic.js`, `fourrow.js` | Four in a Row rules (`FourRowLogic`) and drawing; registers `fourrow` |
| `tictactoe-logic.js`, `tictactoe.js` | Tic-tac-toe rules (`TicTacToeLogic`) and drawing; registers `tictactoe` |
| `checkers-logic.js`, `checkers.js` | Checkers (English draughts) rules (`CheckersLogic`) and drawing; registers `checkers` |
| `reversi-logic.js`, `reversi.js` | Reversi rules (`ReversiLogic`) and drawing; registers `reversi` |
| `dots-logic.js`, `dots.js` | Dots and Boxes rules (`DotsLogic`) and drawing; registers `dots` |
| `seabattle-logic.js`, `seabattle.js` | Sea Battle rules (`SeaBattleLogic`) and drawing; registers `seabattle` |
| `dicekit.js` | `window.DiceKit` (or `module.exports`): what wave 8's dice games share — `DiceKit.game(R)` makes a game's Logic from its rules (modes, seats and the computer, rolling, moves a roll leaves no choice about, the cursor, scores, saving, the turn-by-turn picture), `DiceKit.session(canvas, opts, spec)` the die, the line under the board, the end and the roll sent to the server |
| `ludo-logic.js`, `ludo.js` | Ludo rules (`LudoLogic`) and drawing; registers `ludo` |
| `snakes-logic.js`, `snakes.js` | Snakes and Ladders rules (`SnakesLogic`) and drawing (the board drawn from shapes); registers `snakes` |
| `carrom-logic.js`, `carrom.js` | Carrom rules and whole-number physics (`CarromLogic`) and drawing; registers `carrom` |
| `chess-logic.js`, `chess.js` | Chess rules and the computer's engine (`ChessLogic`) and drawing; registers `chess` |
| `chess-worker.js` | not a script tag: the Web Worker the chess computer thinks in (`new Worker("games/chess-worker.js?v=…")`, which `importScripts` boardkit.js and chess-logic.js with the same `?v=`) |

## The contract with the shell

**Game definition** (from `ArcadeGames.get(id)` / `list()`):
`{ id, name, modes: [{ id, label }], defaultMode, controls: "dpad" | "paddle" | "buttons" | "touch", buttons, padLabel, players, tap, help, stateVersion, turns, turnModes, create(canvas, opts) }`

- `turns: true` and `turnModes` (mode ids): the modes played turn by turn from two (or more) phones only (SPEC §13.5;
  `games.py` `turns.modes` names the same). The game is created with `opts.turns = { seat, picture, send(move) }`
  (below) and the instance's `turnSync(picture)` takes each new picture.

- `players: 2` means "two people on one screen" (W A S D is player 2).

- `dpad`: the shell draws an arrow pad and turns swipes on the game into `up`/`down`/`left`/`right`; with `tap`
  set, a touch that doesn't swipe sends that action (Road Hop: `up`).
- `paddle`: a drag strip and one button (`padLabel`, default "Launch") that sends `fire`; the game gets the canvas
  pointer (and mouse moves without a button).
- `buttons`: the shell draws the game's `buttons` (`[{ action, label, aria, wide, place }]`, held while pressed)
  — one wrapping row, or a grid when every button has `place: [col, row, colSpan, rowSpan]` — and the game gets
  the canvas pointer (`down` / `move` while pressed / `up`).
- `touch`: like `buttons`, but the buttons are optional (the game is played on the canvas itself).
- Actions: `up`, `down`, `left`, `right`, `fire`, `alt` (C, F or Shift; a controller's X/Y), the puzzle games' button
  actions (`n1`–`n9`, `notes`, `fill`, `hint`, `undo`, `erase`, `auto`, and `shuffle` for Tile Match) and, for
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
| Word Guess | `wordguess` | `classic` Six tries, `easy` Eight tries, `strict` Strict | `classic` | `touch`, `typed` |
| Word Search | `wordsearch` | `little`, `kids`, `family`, `puzzler` | `kids` | `touch` Hint |
| Snake Duel | `snakeduel` | `cpu` Against the computer, `two` Two players | `cpu` | `touch`, two players |
| Bubble Pop | `bubbles` | `classic` Classic, `relaxed` Relaxed (gentle), `puzzle` Puzzles, `endless` Endless | `classic` | `paddle` (Shoot) |
| Gem Swap | `gems` | `timed` Timed (90 s), `moves` Moves (levels), `zen` Zen (no clock) | `timed` | `touch` Hint |
| Tower Stack | `stack` | `classic` Classic, `fast` Fast, `easy` Easy (3 tries), `towers` Towers | `classic` | `buttons` Drop |
| Runner | `runner` | `classic` Endless, `easy` Easy (3 lives), `courses` Courses | `classic` | `buttons` ▼ Duck, ▲ Jump |
| Lander | `lander` | `classic` Classic, `easy` Easy, `levels` Levels | `classic` | `buttons` ⟲ ⟳ ▲ Engine (`place` grid) |
| City Defense | `defense` | `classic` Classic, `easy` Easy, `waves` Waves | `classic` | `touch` |
| Slide Puzzle | `slide` | `three` 3 × 3, `four` 4 × 4, `five` 5 × 5, `picture` Picture 3 × 3 (gentle), `picture4` Picture 4 × 4 | `four` | `touch` 👁 Peek (held, `alt`) |
| Lights Out | `lights` | `little` Little 3 × 3 (gentle), `classic` 5 × 5, `big` 7 × 7, `climb` Climb (3 × 3 to 7 × 7) | `classic` | `touch` 💡 Hint (`alt`) |
| Picture Logic | `nonogram` | `five` 5 × 5 (gentle), `eight` 8 × 8, `ten` 10 × 10, `fifteen` 15 × 15 | `ten` | `touch` ■ / ✕ (`notes`), Hint, Undo; `typed` |
| Tile Match | `tiles` | `little` Little (gentle), `classic` Classic, `big` Big heap | `classic` | `touch` Hint, Shuffle (`shuffle`), Undo; `typed` |
| Code Breaker | `codebreak` | `little` Little (3 of 4, gentle), `classic` Classic (4 of 6), `norepeat` No repeats (4 of 6), `master` Master (5 of 8) | `classic` | `touch`, `typed` |
| Type Rain | `typerain` | `letters` Little ones (letters), `easy` Easy (short words), `classic` Classic, `stages` Stages | `classic` | `touch`, `typed` |
| Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle | `fourrow`, `tictactoe`, `checkers`, `reversi`, `dots`, `seabattle` | `easy` Computer · Easy (gentle), `medium` Computer · Medium, `hard` Computer · Hard, `two` Two players (one screen), `phones` Two phones (turn by turn) | `easy` | `touch` (no buttons; cursor keys + Space); options: Dots and Boxes `size` 3 / 4 / 5 (4), Sea Battle `fleet` classic / small (classic) |

The shell hides `powerups` when the App setting "Brick Breaker power-ups" is off.

**Racing on two phones** (spec/SPEC.md §13.3): a definition's `race` field (default `true`, `false` for `players: 2` and
for a game that opts out) says the game can be played in a race: both phones create the game with the same
`opts.seed`, `opts.mode` and `opts.levels` (so a game's randomness must come only from the seed), each sends its
`status()` a few times a second and its final result like any game. Which games the server offers a race for, and
how the winner is decided, is the game's `race` entry in `app/games.py`.

**`create(canvas, opts)`** — `opts = { mode, look, sound, reduceMotion, handedness, seed, onScore(score, level), onEnd(result), onEvent(type, data), turns }`.
`turns = { seat, picture, send(move) → Promise }` plays a turn-by-turn match (wave 7): the board comes from `picture`
(`GET /api/matches/{id}/turns`), a move made here goes to `send` (the shell posts it; the server's answer comes back
through `inst.turnSync(picture)`), and the game ends when a picture says the match is over.
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
| `stop()` | A match the server ended (someone left, …): stops where it is, without `onEnd`. |
| `turnSync(picture)` | Turn by turn: the match as the server now has it (a move made here or on the other phone, or its end); the game rebuilds its board, plays the move's sound, and ends when it's over. |
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
`gameover`; Snake Duel `go`, `eat`, `crash`, `round`, `match`, `level`, `win`, `gameover`; Tank Battle with two tanks
also `round` (against each other), and `fire` / `hit` / `lifeLost` say which `player`; Paddle Duel · Two phones `point`
and `paddle` say which `player`; Bubble Pop `shoot`, `bounce`, `stick`, `fizzle`, `pop`, `clear`, `lower`, `push`,
`swapNext`, `low`, `refill`, `level`, `win`, `gameover`; Gem Swap `cursor`, `pick`, `swap`, `nope`, `match`, `cascade`,
`special`, `shuffle`, `hint`, `hurry`, `clear`, `level`, `win`, `gameover`; Tower Stack `place`, `cut`, `perfect`, `miss`,
`tower`, `level`, `win`, `gameover`; Runner `jump`, `land`, `coin`, `hit`, `course`, `level`, `win`, `gameover`; Lander
`land`, `crash`, `empty`, `retry`, `level`, `win`, `gameover`; City Defense `fire`, `burst`, `hit`, `flier`, `flierHit`,
`split`, `thud`, `cityLost`, `baseLost`, `lastCity`, `empty`, `clear`, `rebuild`, `level`, `win`, `gameover`; Slide Puzzle
`slide`, `nope`, `peek`, `win`; Lights Out `press`, `cursor`, `hint`, `nohint`, `clear`, `level`, `win`; Picture Logic `fill`,
`cross`, `clear`, `wrong`, `cursor`, `mode`, `hint`, `nohint`, `undo`, `check`, `win`; Tile Match `select`, `unselect`, `match`,
`nomatch`, `blocked`, `cursor`, `hint`, `shuffle`, `undo`, `stuck`, `win`; Code Breaker `place`, `clear`, `slot`, `cursor`, `refuse`,
`guess`, `win`, `lose`; Type Rain `spawn`, `key`, `word`, `slip`, `letgo`, `land`, `stage`, `level`, `win`, `gameover`; the wave 7
board games `move`, `cursor`, `nope`, `send` (a move on its way to the server), `win`, `lose`, `draw`, `ready` (pass the
phone), and Four in a Row `drop`, `line`; Tic-tac-toe `mark`, `line`; Checkers `step`, `capture`, `king`, `pick`; Reversi
`place`, `pass`; Dots and Boxes `line`, `box`, `again`; Sea Battle `placed`, `shuffle`, `turnship`, `moveship`, `pick`, `hit`,
`miss`, `sunk`. A callback that
throws is logged and doesn't stop the game.

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
Bubble Pop: `shots, popped, dropped, boards, bestPop, won, cause ("low" | "won"), mode`.
Gem Swap: `swaps, gems, bestChain, lines, blasts, stars, locks, shuffles, levels, won, cause ("time" | "moves" | "won"), mode`.
Tower Stack: `floors, perfects, bestStreak, towers, misses, livesLeft, won, cause ("miss" | "won"), mode`.
Runner: `distance, coins, jumps, hits, courses, livesLeft, won, cause ("box" | "pit" | "bar" | "flier" | "won"), mode`.
Lander: `landings, crashes, fuelUsed, best, levels, livesLeft, won, cause ("ground" | "tilt" | "fast" | "won"), mode`.
City Defense: `missiles, fliers, shots, waves, citiesLeft, citiesLost, won, cause ("cities" | "won"), mode`.
Slide Puzzle: `won, cause, mode, moves, slides, size, summary`. Lights Out: `won, cause, mode, moves, hints, boards, fewest, summary`.
Picture Logic: `won, cause, mode, size, hints, mistakes, givens, effective, filled, crossed, summary`.
Tile Match: `won, cause, mode, pairs, left, hints, shuffles, undone, effective, summary`.
Code Breaker: `won, cause ("won" | "rows"), mode, rows, maxRows, code (at the end), summary`.
Type Rain: `typed, letters, slips, landed, bestRun, stages, wpm, livesLeft, won, cause ("landed" | "won"), mode, summary`.
Wave 7: `won, lost, draw, winner (0 | 1 | −1), mode, moves, summary, notSaved` (why a score of 0 isn't kept, for the shell's badge).

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
  1's score: food 10, round 100, match 500 × min(arena, 10) (`score2` the same for player 2). Against the computer
  the arenas come in order and losing a match ends the game; two players (one screen) play one match on an arena
  from the seed.
  On one phone, swipes on the right half steer green and on the left half blue. Every mode plays the arena list
  (walls, speed, foods).
- **Paddle Duel · Two phones** (`duel-logic.js`, mode `phones`): seat 1's paddle at the bottom (top edge 266), seat
  2's at the top (bottom edge 54); the court is the same from either end (y ↔ 320 − y; the ball is out above 34 or
  below 286). Everything in whole numbers: positions and speeds in 1/256 px, the angle from where the ball meets
  the paddle in 21 steps of 5.5° from a table of sines (× 4096), rounded the same way on either side, so seat 2's
  game is exactly seat 1's turned round. The ball starts at 2.4 px an update (+0.12 a return, at most 6), served
  after 1 s (or at once by the receiver) to whoever lost the point — the first to a player chosen by the seed, at
  up to ±27.5°. A finger sends `aim` (its x on the court; seat 2's turned round), the keys left/right held. First to
  7: a return 10, a point 100, the match 1,000.
- **Tank Battle · two tanks** (`tanks-logic.js`, modes `together` and `against`): player 2's tank (`p2`, its controls
  in `ctl2`, its shells owner −1) starts right of the flag (together) or at the top facing down (against). Together:
  1½ × the arena's enemies (rounded up), who chase the nearer tank; each player's own lives (3) and score (a tank
  100 to whoever hit it, an arena 500 to both); friendly shells stop on the other tank; a player out of lives
  stays out (also in the next arena); over when the flag falls or both are out. Against each other: no enemies,
  `VERSUS_LEVELS` (four arenas whose bottom half is the top half turned round), the first from the seed and the
  next each round; `FLAG2` at the top is player 2's; a hit 100 (the other tank's shield still protects it), a
  round 300 (their flag — any shell, theirs too — or all their lives; 3 lives a round), the match 500 (first to 2
  rounds; at most 5 rounds, a round at most 2 minutes, then drawn). `steer` = 1 + the direction toward a finger
  (worked out on the phone with `steerFor`). Single-player Tank Battle is unchanged.
- **Bubble Pop** (`bubbles-logic.js`): rows of 8 bubbles (24 px, every other row half a bubble to the right) hang
  from the ceiling; the launcher at the bottom turns 1.6° an update (←/→ held) up to 78° either way, or points at a
  finger (above the launcher; on the strip below it the finger's x sets the angle). A shot flies 7 px an update in
  2 px sub-steps, bounces off the side walls and sticks next to the bubble or ceiling it touches; three or more of a
  colour touching pop (10 each) and everything no longer hanging from the ceiling drops (20 each). The next shot is
  ready 10 updates after the last one sticks. The bubble to shoot and the next one are always colours still on the board (↓ or C, or a
  tap on the next bubble, swaps them). Every `drop` shots the ceiling comes down a row (*Endless*: a new row pushes in
  from the top instead); a bubble below the dashed line ends the game (`cause: "low"`). Clearing the board scores
  500 and brings the next (1.5 s). *Classic*: boards from the seed, 3–6 colours and the ceiling every 9 down to 5 shots
  as the boards go on; *Relaxed* (gentle, for small children): at most 4 colours, at most 6 rows, the ceiling every 14
  shots; *Puzzles*: the puzzle list (`rows`, `colours`, `drop`; every bubble hangs from the top row); *Endless*: 4–6
  colours, a new row every 8 down to 4 shots, never cleared. Each colour has its own mark too (none, a line across, a
  dot, a line down, a cross, a peak), so colours are never told apart by colour alone (also on Retro LCD).
- **Gem Swap** (`gems-logic.js`): 8 × 8 gems of 4–6 kinds (each its own shape and colour). Swapping two neighbours
  (8 updates) that makes a line of three or more clears it (12 updates); a swap that doesn't swaps back. Gems fall
  4 px an update and new ones drop in from the seed; each further clear in a row is a cascade (10 a gem × the cascade
  step, at most × 5). Four in a line leaves a line gem (its row or column), an L or T a blast gem (3 × 3), five a star
  (swapped: every gem of that kind; two stars: the most common kind); making one scores 50 / 100 / 200. Locked gems
  can't move; a clear through one unlocks it. No move left: the board is shuffled (40 updates). The board never
  starts with a line. *Timed*: 90 s (a "hurry" at 10 s); *Moves*: the level list — reach the level's `goal` points and
  break its `locks` within its `moves` (50 a move left), the last level wins; *Zen*: no clock, no end (End game keeps
  the score). Keys: the arrows move a ring, Space picks a gem up and an arrow swaps it; `alt` (Hint) rings a move for
  2 s.
- **Tower Stack** (`stack-logic.js`): a 14 px block slides between x 8 and 232 at the tower's speed (+ `ramp` a floor,
  at most 5 px an update); a press drops it (at least 10 updates after it appears). The overhang is cut off and
  falls; within 3 px of the floor below is a perfect drop that lines up exactly (10 + 10 × the run of perfect drops,
  at most 5), and from the third perfect drop in a row the block grows back 4 px (when the tower allows). A floor
  scores 10; a level every 10 floors. Missing the tower completely ends the game — in *Easy* (150 wide, speed 1, three
  tries) and *Towers* it costs one of 3 lives and that floor is tried again. *Classic* 120 wide at 1.3, *Fast* 100 at
  2.2. *Towers*: the tower list (`floors`, `width`, `speed`, `ramp`, `grow`), a tower 200 and the next one after
  1.5 s; the last wins. The camera follows the top of the tower.
- **Runner** (`runner-logic.js`): the runner runs right on its own (3.6 px an update, *Easy* 3, +0.3 every 3,000 px,
  at most 8 / 6.5); the world is 60 px segments holding at most one thing: a box, a tall box, a pit, a hanging bar,
  a flier, or coins on the ground or in an arc (10 each). After a hazard the next is at least `ceil(50 × speed / 60)`
  segments on, so there is always room to land and jump again (a test runs every speed). Jump 7.4 px an update up
  (held: less gravity for up to 12 updates — a higher jump), ↓ ducks (in the air: falls faster). 1 point a 10 px. A
  hit or a pit costs a life (*Endless* 1, *Easy* and *Courses* 3) and 1.5 s of safety. *Courses*: the course list
  (`speed`, `ramp` every 10 segments, a `pattern` of segment letters `. b B p h f c o`), 100 for each finished; the
  last wins. A finger: down jumps (held: higher), dragging down 18 px ducks.
- **Lander** (`lander-logic.js`): gravity (`gravity` px an update²), a sideways `wind`, the engine 0.06 px an update²
  the way the lander points (it turns 3° an update, up to 90°), one fuel an update. The ground is 13 heights 20 px
  apart; pads are flat stretches marked ×1–×5 (the small ones pay more). Touching down on a pad within 10° of upright,
  at most 0.9 px an update down and 0.6 sideways lands: multiplier × 100 + half the fuel left, and the next level
  after 2 s; anything else crashes and costs one of 3 lives (the level again). The HUD's speeds turn green when a
  landing would be safe. *Classic* and *Easy* (lighter gravity, wide ×1 pads, no wind, lots of fuel) make levels from
  the seed; *Levels* plays the list (`heights`, `pads`, `start`, `drift`, `gravity`, `wind`, `fuel` — enough fuel for a
  full stop from the highest start, checked); the last wins. Fuel left breaks a race between two good landings.
- **City Defense** (`defense-logic.js`): six cities and three bases (left, middle, right; the middle one's shots are
  faster) along the ground. A tap (or the crosshair, moved 3 px an update by the arrows, and Space) sends an
  interceptor from the nearest base with ammo to that point (at most 8 in the air); it bursts into a cloud that grows
  to 20 px, holds and shrinks, and stops every missile and flier inside it (25 / 100). Some missiles split into three
  on the way down; fliers cross the sky and drop missiles of their own. Missiles aim at cities and bases (a base that is
  hit is out for the rest of the wave). A wave ends when everything it sent is gone: 5 for each shot left, 100 for each city
  standing; the bases are refilled, and every third wave a lost city is rebuilt. No city left ends the game.
  *Classic* and *Easy* (slower, fewer missiles, 12 shots a base) make their waves endlessly; *Waves* plays the list
  (`missiles`, `speed`, `splits`, `fliers`, `ammo`, with enough ammo for what comes); the last wins.
- **Slide Puzzle** (`slide-logic.js`): an n × n tray (3, 4 or 5) of tiles and one gap. A tap on a tile in the gap's row or
  column slides it and the tiles between into the gap; an arrow (or a swipe of 12 px) slides the tile on that side of the
  gap that way (↑: the tile below the gap moves up). Each tile moved is a move. The shuffle is a random order with the gap at
  the bottom right, made even by swapping two tiles when it comes out odd (so it can always be solved), at least a set
  distance from solved (the sum of every tile's rows and columns from home: 8 / 24 / 50). Solved: the score is 10,000 −
  moves (at least 10), so the fewest moves rank first; unsolved scores 0. *Picture* modes show one of six pictures drawn
  from shapes (a house, a boat, a rocket, a flower, a fish, a balloon), picked by the seed, with small numbers in the
  corners (the start screen's **Numbers on picture tiles**); **Peek** (held: the button, C or a controller's X) shows the
  finished picture or the numbers in order. A slide animates over 6 updates (none with reduce motion).
- **Lights Out** (`lights-logic.js`): pressing a light switches it and its four neighbours. A board is made from the seed by
  pressing 3–5 (3 × 3) … 10–18 (7 × 7) random lights on a dark board (so it can be solved), with at least 3 / 4 / 6 / 8 / 10
  presses needed; the fewest presses are worked out exactly (Gaussian elimination over on/off, then the smallest of the
  equivalent answers — 5 × 5 has 4, 4 × 4 16). Score: 10,000 − presses − 5 a hint once every light is off; unsolved 0.
  *Climb* plays 3 × 3, 4 × 4, 5 × 5, 6 × 6 and 7 × 7 with a 75-update pause between (the last wins; level = the board).
  The start screen's **Hints** (off by default, and always off in races, `offInRaces`): Hint (`alt`) rings a light from the
  fewest presses, the one nearest the cursor. Keys: the arrows move a cursor (wrapping), Space presses.
- **Picture Logic** (`nonogram-logic.js`): numbers beside each row and above each column give its runs of filled squares.
  A puzzle is a random pattern from the seed (density 0.52–0.6; smoothed into blobs once on 8 × 8 and 10 × 10, twice on 15 ×
  15; mirrored half the time; 30–75 % filled, at most one empty line) that a line solver — every row and column on its own,
  from what the numbers allow (a dynamic programme over the runs) — works out completely; 40 tries are made and, when none
  works out, squares of the answer are given at the start (fixed, shown with a dot) until it does. So every puzzle has
  exactly one answer and needs no guessing. A tap fills a square (or crosses it: the ■ / ✕ button, `notes`, or M) and tapping
  it again empties it; dragging carries the same change along the row or column it started in, to squares that were like
  the first; Undo takes back a whole stroke. Mistakes (start screen, like Sudoku): *shown at once* — a wrong fill or cross
  is put right, fixed and adds 10 s — or *when the grid has as many filled squares as the answer*, then the wrong ones are
  marked. Hint (3 a puzzle, +30 s): puts a wrong square right first, else fills in a square the numbers decide (the line
  solver's next step from what is right so far). Numbers dim when their line already matches. Solved when the filled
  squares are exactly the answer (crosses don't matter): 10,000 − the counted seconds (time + penalties), at least 10.
  Keys: arrows, Space fills, X / `alt` crosses, M switches, H hint, U / Z undo. The layout (number widths, square size) is
  worked out from the puzzle and the look's font, so 15 × 15 still fits the coarse looks.
- **Tile Match** (`tiles-logic.js`): tiles lie in layers (positions in half-tile units; *Little* 20 tiles in 2 layers, *Classic*
  72 in 3, *Big heap* 104 in 4), four of each symbol. A tile is free when no tile lies on it and its left or right side is
  open; two free tiles with the same symbol go together. A deal is made by clearing the empty heap first — taking pairs of
  places free at that moment and giving each pair the next symbol (from a shuffled list) — so that order clears it and
  every deal can be cleared; two tiles of one symbol are never dealt on top of each other (the last pair could otherwise
  end up stacked, a dead end no shuffle mends). Hint rings a free pair for 2.5 s (+15 s); Shuffle deals the tiles left the same way (+30 s);
  Undo puts the last pair back (free; a pair keeps its own symbol, so it works after a shuffle too). With no free pair left
  the game says so (Shuffle or Undo). Cleared: 10,000 − the counted seconds, at least 10. Keys: the arrows move a ring to
  the nearest tile that way, Space picks, H / `alt` hint, S shuffle, U / Z undo. Symbols (tiles.js): *Little* a circle,
  square, triangle, star and diamond; the others a number 1–9 with a suit shape under it (circle, diamond, triangle), each
  suit its own colour too.
- **Code Breaker** (`codebreak-logic.js`): a hidden code of 3–5 symbols from 4–8 (each a number, a colour and a shape:
  circle, square, triangle, diamond, star, hexagon, plus, a triangle pointing down). A row is checked when full: a solid dot
  for each symbol in the right place, a hollow ring for each other symbol in the code but elsewhere (each code symbol
  counted once). Modes: *Little* 3 of 4, no repeats, 8 rows; *Classic* 4 of 6, repeats, 10 rows; *No repeats* 4 of 6, 10
  rows (a row with a symbol twice is refused); *Master* 5 of 8, repeats, 12 rows. Cracked: 1,000 × (rows left + 1) +
  (999 − seconds, at least 0); otherwise 0 and the code is shown. Keys 1–8, Enter, Backspace; a controller: ← → pick on the
  keys, A places, ↑ checks, ↓ (or X) deletes.
- **Type Rain** (`typerain-logic.js`): words fall from y 46 to the ground at 196 (words of 7 letters or more at 85 % of the
  speed). The first letter typed picks the lowest word starting with it, then its letters in order; a wrong letter is a slip
  (the run ends, the word stays picked); Backspace lets go. A word: 10 a letter × 1, 2 (from 10 words in a row without a slip
  or a landing) or 3 (from 25). A landed word costs a life (3; *Little ones* 5). What falls (the word, where, when) comes only
  from the seed and the clock — never from the typing — so both phones of a race get the same words in the same order:
  *Little ones* single letters, *Easy* 3–4 letters, *Classic* 3–4 letters at first and up to 10 (longest 4 + level ÷ 2,
  shortest 3 + level ÷ 4, at most 6), a level every 20 s (up to 30) making them faster (0.3 + 0.035 a level, at most 1.3 px an
  update) and closer (130 updates apart − 4 a level, at least 40). *Stages* plays the stage list (`words`, `speed`, `gap`,
  `shortest`, `longest`; 1 and 1 = letters; a word must take at least 1 + 0.35 × longest seconds to fall); each stage's words
  come from the seed and the stage number, a cleared stage scores 200 and the next follows after 2 s; the last wins. The
  keyboard on the canvas lights the key for the next letter (and, in the gentle modes, the lowest word's first letter);
  a controller (or the arrow keys) moves a ring over it, A (Space) types the ringed key and X lets go of a word.
- **The wave 7 board games** (`boardkit.js` + `<game>-logic.js`): two players, seat 0 and seat 1. Against the computer
  you are seat 0 and move first, the computer (seat 1) moves `CPU_WAIT` (36) updates after it becomes its turn; on one
  screen seat 0 moves first; from two phones who moves first comes from the seed (`seed % 2`, as the server's rules).
  The rules are the server's (`app/rules/<game>.py`) move for move: the same moves (canonical JSON), the same
  refusals, the same state (`digest`) — checked on 2,400 random games. A move is made with a tap on the board or the
  cursor (arrows) and `fire`; an illegal one says so on the bottom line (`nope`). Score: a win the level's base
  (`LEVEL_BASE` 100 / 250 / 500, two phones `PHONES_BASE` 500) + a bonus up to the base, a draw base ÷ 4, a loss and
  one screen 0. The game ends 80 updates after the last move (the winning line shows first).
  - **Four in a Row** (`fourrow-logic.js`): 7 × 6, `{col}`; four in any line wins, a full frame draws; ↓ drops too.
    Computer: Easy wins when it can, blocks 60 % of the time, else near the middle at random; Medium and Hard search 4
    and 7 moves ahead (negamax with alpha-beta, scoring the open fours; one more ply when needed so the leaves have as
    many discs of each colour), ties from the seed. Bonus 10 an empty place.
  - **Tic-tac-toe**: `{cell}` 0–8; ✕ moves first. Easy takes wins, blocks half the time; Medium always wins / blocks
    and likes the middle; Hard is perfect (minimax). Bonus 25 an empty square.
  - **Checkers** — English draughts: 8 × 8, dark squares; the first mover is dark at the bottom; men one square
    diagonally forward, kings both ways; captures compulsory; a jump that can continue must continue (any capture may
    be chosen, not necessarily the longest); jumped pieces leave at the end and can't be jumped twice; reaching the
    far row crowns and ends the move; no legal move loses; 80 plies without a capture or a man moving draws.
    `{path: [from, to, …]}`. Taps: a piece (dots: where it can go), then each landing square (finished by itself
    when one way on is left); `alt` lets go. The second phone of a match draws the board turned round (its arrows
    too). Easy plays any legal move; Medium 3 plies counting pieces (king 175, man 100); Hard 6 plies, also advanced
    men, the middle and the back row. Bonus 30 a piece left.
  - **Reversi**: `{cell}`; a disc must flip; no place → the turn passes automatically (`passed`, a `pass` event);
    neither → over, more discs wins. Easy random; Medium the best square by a value table; Hard 4 plies with the table
    and mobility. Bonus 5 a disc more.
  - **Dots and Boxes**: `size` 3–5 boxes; `{edge}` (across lines first, row by row, then up-and-down); a box's fourth
    side claims it and the same player goes again. The cursor walks the lines (← → along, ↑ ↓ to the crossing ones).
    Easy takes a box 75 % of the time, else any line; Medium always takes, avoids third sides, then gives away the
    fewest; Hard also leaves the last two boxes of a run (a double-cross) to keep control. Bonus 20 a box more.
  - **Sea Battle**: `fleet` classic (10 × 10: 5 4 3 3 2) or small (8 × 8: 4 3 3 2); ships may touch, never overlap;
    `{place: [[row, col, length, 0 across | 1 down] …]}` once each (both place in any order), then `{shot}`
    alternating (a hit doesn't shoot again); the server's record of a shot adds `hit`, `sunk`, `cells`. Placing: a
    random arrangement from the seed; tap a ship to pick, again to turn, a square to move it there; Shuffle / Turn /
    Ready are drawn on the canvas and are on the cursor's path (below the sea). One screen: after each move the
    result shows `PASS_DELAY` (70) updates, then "Pass to …" until a tap. Two phones: the state comes from the
    server's `view` (`fromView`) — the other fleet is never known here; a shot's result comes back from the server.
    The computer only reads its own shots' results: Easy at random (35 % next to a hit), Medium hunts then follows
    hits along a line, Hard also hunts where the ships still afloat fit most often. Bonus 15 a square unhit.
- All of them stay inside the server's honest-score limits during play, also with the hardest levels their
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

- Definition fields `options` (start-screen choices stored per person, passed as `opts.options`; Sudoku: Show mistakes, Number lines (three kinds)) and `typed: true` (the shell sends physical keys as `input("key:A" | "key:ENTER" | "key:BACKSPACE", true)`, and P no longer pauses). A `preview` opt makes the still picture behind the start card.
- Daily play passes `opts.seed` and a mode from the day's challenge; none of the three have level lists, so no `level_kinds` entry (the puzzles are made from the seed).
- Results carry `stats.summary` and `stats.won`; scores are higher-is-better. `unfinished_zero` games score nothing until solved.

## Sudoku: number lines

When a number is in focus (a filled cell is tapped, or a number is picked on the pad), every cell in the **row**,
the **column** and (by default) the **3 × 3 box** of each cell holding that number gets one even, soft shade — no
line through the middle, no dots.

- **Three kinds** — the start-screen option **Number lines** (id `lines`), saved per person with the other
  start-screen choices (`users.game_prefs`, `PUT /api/prefs {gamePrefs: {sudoku: {lines}}}`):

  | Choice | id | Number lines shade | Selected cell's own tint |
  |---|---|---|---|
  | **Rows, columns and boxes** (default) | `on` | rows, columns and boxes of the number's cells | its row, column and box |
  | **Rows and columns only** | `rows` | rows and columns only, no box | its row and column |
  | **None** | `off` | nothing | none |

  The ids are the ones the old **On / Off** switch saved, so someone who had it on keeps rows, columns and boxes
  and someone who had it off gets None; anything else saved reads as the default (`Logic.lineMode`). In every
  kind the cells holding the focus number keep their strong highlight and the selected cell keeps its fill and
  outline (that is how you see which number and which cell you're on); "None" removes all row/column/box
  shading, including the faint tint around the selected cell. With rows and columns only, the empty cells left
  clear are where the number isn't ruled out by a row or column — its box may still rule some out.
- **A display choice only:** it lives in the game file (`opts.options.lines`), never in the rules' state, so it
  doesn't change the score, a saved game (any saved game resumes with whatever kind is chosen now), a race or a
  daily challenge (each person sees their own choice; it is not `offInRaces`).
- **One shade per cell.** `Logic.lines(s, focus, kind).covered` (81 booleans; `null` with no focus or kind
  `off`) is worked out first and each covered cell is filled once, so crossings don't get darker. The selected
  cell's own row/column/box tint (`Logic.linked(sel, i, kind)`) is skipped where the number lines already shade.
- **What stands out:** the cells holding the focus number (the strong highlight), the **empty cells left clear**
  (the only places the number can still go), and the selected cell's outline on top.
- **Looks:** the shade is palette colour 7 (each look's accent) at low strength, filled straight on the context
  with no outline: 0.2 (Modern, Paper), 0.3 (High contrast), 0.08 on Neon so the glow doesn't bloom; Pixel uses
  the same per-cell fill on its coarse grid (0.24). Retro LCD has only ink, so its empty covered cells get a
  sparse dither (one dot in sixteen) and filled cells are left plain so the digits stay clear; the focus cells
  are solid ink with a light digit. The low-resolution looks (Retro LCD, Pixel) have no selected-cell tint in
  any kind. All three kinds use the same drawing, so they work in every look.
- **Input:** nothing changes — keyboard, touch and controller pick the focus number the same way; the kind is
  chosen on the start screen (a select, so it works by touch, keyboard and controller like the other options).
- The line under the board says "Number lines: 5" while a number is in focus (not with None).
- **Tests:** the covered set for a sample puzzle for each kind (rows-only = the rows and columns of the number's
  cells, None = null), `lineMode` for old/odd values, `linked`; the option's choices and ids; the saved state is
  identical whatever the kind; the drawing test records every `line` call inside the grid and checks it lies on a
  grid line, and counts one fill per covered cell in each look for each kind (none with None), the focus cells'
  highlight in every kind, and the selected cell's tint (20 / 16 / 0 cells).

## Live duels (removed in 1.8.0)

The two-phone lockstep play of Snake Duel, Paddle Duel, Tank Battle and Carrom (SPEC §13.4) was removed in 1.8.0
with `lockstep.js`, the kit's lockstep session (`opts.live`, `liveStatus()`), the `lockstep` / `liveModes` /
`liveTurns` registration and the games' seat-2 views and input forwarding. The rules files keep their two-player
code (`tanks-logic.js` `two` modes, `duel-logic.js` / `snakeduel-logic.js` / `carrom-logic.js` `phones`), unreachable
from the game files; the logic tests still cover them.

## Wave 5 additions

- The six games are single-player, have a level list played by one mode each (`level_kinds/<game>.py`) and are raced
  (`games.py` `race: {"rule": "score"}`, also in `together.RACE_DEFAULT`): both phones get the same seed and level
  list, so the same boards, gems, blocks, course, terrain and missiles. Lander's score includes half the fuel left,
  so of two equal landings the one with more fuel wins.
- **The same numbers on every browser.** A race only needs both phones to start the same, but the rules go further:
  they use only `+ − × ÷` and `Math.floor/round/sqrt/abs/min/max/imul` (exact everywhere). Bubble Pop's aim and
  Lander's thrust need sines; they are worked out once by their series (`dsin`, only `+ − × ÷`) rather than with
  `Math.sin`, whose last digit may differ between engines. `wave5.test.js` plays every mode with
  `Math.sin/cos/atan2/pow/random/…` and `Date.now` made to throw. (A finger's angle in Bubble Pop uses `Math.atan2` on
  the phone, but it is rounded to tenths of a degree before it reaches the rules.)
- Gentle modes for small children: Bubble Pop *Relaxed*, Tower Stack *Easy*, Runner *Easy*, Lander *Easy*, City
  Defense *Easy*; Gem Swap *Zen* has no clock.

## Wave 6 additions

- The six games are single-player and raced from the seed. Slide Puzzle, Lights Out, Picture Logic, Tile Match and Code
  Breaker make their boards, heaps, pictures and codes from the seed (no level list, like Sudoku: `level_modes` empty,
  `unfinished_zero`); Type Rain has a stage list (`level_kinds/typerain.py`, mode `stages`).
- How a race is decided (`games.py` `race`): Slide Puzzle and Lights Out `{"rule": "fastest", "tiebreak": "score"}` —
  solved first wins, and of two solved in the same second the higher score (fewer moves); their own score ranks the fewest
  moves on the leaderboard. Picture Logic and Tile Match `{"rule": "score"}` with 10,000 − the counted seconds (penalties
  included, so hints aren't free in a race); Code Breaker `{"rule": "score"}` (fewer rows, then faster); Type Rain
  `{"rule": "score", "tiebreak": "faster"}` (the same score: the shorter game, so more words a minute).
- **The same numbers on every browser**: the rules use only `+ − × ÷` and `Math.floor/round/abs/min/max/imul`;
  `wave6.test.js` makes every mode's board and plays it with `Math.sin/cos/pow/random/…` and `Date.now` made to throw.
- A new registry action `shuffle` (Tile Match's button). Picture Logic, Tile Match, Code Breaker and Type Rain are `typed`
  (letters and digits go to the game; Esc pauses). Type Rain and Code Breaker draw their own keys on the canvas for phones,
  as Word Guess does.
- Lights Out's **Hints** option is `offInRaces` with the default *Off*, so a race is always played without hints.
- Gentle modes for small children: Slide Puzzle *Picture 3 × 3*, Lights Out *Little 3 × 3*, Picture Logic *5 × 5*, Tile Match
  *Little*, Code Breaker *Little*, Type Rain *Little ones (letters)*.
- Looks: every game draws in all six looks; the coarse looks get bigger number badges (Slide Puzzle), numbers in a box
  instead of small shapes (Code Breaker), a layout measured with the pixel font (Picture Logic), and solid/outlined
  marks instead of colour (Lights Out on and off, Code Breaker's dots and rings, Tile Match's selection).

## Wave 7 additions — turn by turn

- **Registration**: `turns: true`, `turnModes: ["phones"]`, `race: false`; no levels; `stateVersion` 1 (the two-phone
  mode can't be saved: the session's `save` is undefined there).
- **Logic** (from `BoardKit.game(R)`): `create({mode, seed, options, seat})`, `step`, `press(action, down)`,
  `tap(target)`, `play(seat, move)` (phones: returns `send` instead of playing), `moves`, `legal`, `toMove`, `mySeat` (the
  seat the person at this screen may move now, or −1), `score`, `result`, `save`/`restore`, `fromTurns(picture)` (the
  board from the server: the moves replayed, or Sea Battle's `fromView`), `sync(old, picture)` → `{s, events}`
  (keeping the cursor, Sea Battle's unsent arrangement, and an animation for the other's move), `digest`, and `R` (the
  rules: `init`, `moves`, `apply`, `digest`, `bonus`, `ai(s, seat, level)`, cursor helpers, `tap`, optional `toMove`,
  `legal`, `step`, `fromView`, `keep`, `passDelay`, `check`, `summary`).
- **Drawing** (`BoardKit.session`): the two players at the top (a mark, the name, the count; a line under whose move it
  is), the board (the game's `drawBoard`), the bottom line (whose move / the computer thinking / sending / waiting for
  the other / what just happened), the end banner, "Pass to …". Pieces always differ by shape, not only colour: Four in
  a Row's second disc has a ring, Checkers' light pieces a light middle and kings a star, Reversi's light discs a ring,
  Dots and Boxes' boxes a round or a diamond mark, Sea Battle crosses and dots; Retro LCD solid and hollow.
  `BoardKit.toneCi` picks "dark" and "light" piece colours that stay dark and light in every look (Modern's dark theme
  swaps ink and paper).
- **The same rules on both sides**: only `+ − × ÷` and exact `Math` functions; `tests/js/turns-fuzz.js` plays random
  games with the JavaScript rules for `tests/test_wave7.py` to replay through the Python rules.

## Wave 8 additions — dice, Carrom and Chess

- **Registration**: Ludo and Snakes and Ladders `turns: true`, `turnModes: ["phones"]`, modes `cpu` (*You and the
  computer*), `pass` (*One phone (pass and play)*), `phones` (*Phones (turn by turn, 2–4)*), options `players` 2/3/4
  (Ludo 4, Snakes and Ladders 2 by default) and `fill` (*One phone: empty seats* — left empty, or the computer fills
  them up to four); Snakes and Ladders also `board` (classic / gentle) and `finish` (any / exact), which are the
  match's options from phones (the server's `OPTIONS`). Chess `turns: true` with wave 7's modes and the option
  `side` (against the computer: White, Black, or either from the seed). All `race: false`, no levels, `stateVersion`
  1; the phone modes can't be saved.
- **Dice from the server**: from phones the die is never rolled in the browser. Tapping the die (or Space) sends
  `opts.turns.roll()` (play.js: `POST /api/matches/{id}/roll`), and the picture in the answer is drawn (with the
  token's hop animated). On one phone the die comes from the seed (`rand(s)`), the same for a seed.
- **Rule sets** (the server's and the browser's rules are the same, move for move):
  - *Ludo*: 52-square track, 4 tokens each, a 6 to come out onto your start, clockwise then up your own 5-square
    home column, home only with the exact roll; landing on other colours' tokens sends them all home except on the 8
    safe squares (the four starts and the stars eight squares on); your own tokens may share; a 6 gives another roll,
    a third 6 in a row loses the turn; a roll no token can use is passed; the first with all four home wins, the
    rest placed by tokens home, then progress. Colours: 2 players red and yellow (opposite), 3 red, green, yellow, 4
    all. The computer: capture, then home, then out of the yard, then into the home column or onto a safe square,
    else the furthest token.
  - *Snakes and Ladders*: 1–100, boustrophedon, everyone starts off the board; ladders up, snakes down (never
    chained); no extra rolls; Finish *Reach 100* (passing it wins too — kind to small children) or *Exactly on 100*
    (a roll that would pass it is lost). Two original boards: Classic (9 ladders, 10 snakes), Gentle (7 ladders, 6
    short snakes, ≤ 12 squares). The board, ladders and snakes are drawn from shapes.
  - *Carrom* (a common family rule set): 9 white, 9 black and the red queen; the first to shoot plays White and
    breaks; pocketing your own coin or the queen gives another shot; the queen must be covered by one of yours in the
    same or the next shot or it goes back; pocketing the striker is a foul (the shot's coins back, plus one of yours
    as a penalty); your last coin can't go down while the queen is on the board; the side that pockets all nine wins
    the board and scores the other side's coins left + 3 for the queen; a board still going after 300 shots ends on
    points (coins pocketed, + 3 for a covered queen), equal points a draw. Doubles: four on one screen, partners
    opposite, turns going round. Shot = `[place 0–1000, angle 100–1700 tenths of a degree (900 straight ahead), power
    1–100]`. Physics: whole numbers (1/256 px, 4 sub-steps an update, exact integer division and square roots, a sine
    table built from its series, no `Math.sin`/`random`), FNV-1a checksum. The computer tries cuts of each coin into
    each pocket from seven places, the same off each cushion and a few seeded shots, on a copy, and keeps the best
    (Easy 6 tried and aimed a little off, Medium 16, Hard 46; of shots that pocket nothing, the one leaving its coins
    nearer the pockets).
  - *Chess*: the FIDE moves and automatic draws (SPEC §13.5); promotion always asks (Queen, Rook, Bishop, Knight).
    The engine: negamax alpha-beta, iterative deepening, quiescence, a transposition table with fixed Zobrist keys,
    MVV-LVA / killer / history ordering, material + piece-square evaluation; strength by node budgets, never the clock,
    so a position always gets the same move (Easy 2 plies choosing among moves within ~1.5 pawns, Medium 3 plies,
    Hard up to 7 plies in 220,000 nodes, ≈ 1 s on a phone). It thinks in `chess-worker.js` (same origin, allowed by
    the strict CSP); without a worker, on the page in a timer. Perft numbers (start position to depth 4, Kiwipete and
    positions 3–5) pass in both languages.
- **Looks**: tokens and pieces differ by shape as well as colour (Ludo red ●, green ■, yellow ▲, blue ◆; Carrom's
  coins hollow / solid / dotted on Retro LCD; chess pieces drawn from shapes, Black's turned-round view at the bottom
  for whoever plays Black); the die face is drawn light with dark pips in every look; the coarse looks (Pixel, Retro
  LCD) get shorter status lines and names ("CPU 2").
- **Agreement**: `tests/js/wave8-fuzz.js` plays random Ludo, Snakes and Ladders and Chess games with the JavaScript
  rules for `tests/test_wave8.py` to replay through the Python rules (legal moves before each move, the state after).

## Wave 9 — Arrow Release

A calm puzzle in the style of the "tap the arrows away" games: the board is full of arrows, and the aim is to release
them all without crashing one into another.

- **Files**: `arrows-logic.js` (`ArrowsLogic`: boards from the seed, the rules, the score) and `arrows.js` (drawing,
  input); registers `arrows`. Server entry `arrows` in `games.py`: name **Arrow Release**, icon 🏹, tags `puzzle`,
  `gentle`.
- **The board**: a grid with arrows on it. Each arrow covers one or more cells in a line and points one way along
  it (*Twisty*: arrows may bend — a path of cells with the head at one end, like a short snake). Some cells may be
  empty.
- **Releasing**: tap (or select and press) an arrow. If every cell from its head to the edge of the board, in the
  direction the head points, is empty, the arrow slides out head first — the body follows the head — and leaves
  the board (+1 released). If something is in the way, the arrow slides up to it, bumps and goes back to where it
  was: a **mistake**, and one heart lost.
- **Hearts**: 3. The game ends when the last heart is lost (not cleared) or when every arrow is gone (cleared).
  *Little* has no hearts: a bump only wiggles the arrow.
- **Always solvable, and order never traps you**: releasing an arrow only ever frees cells, so any arrow that can
  leave now can always leave later — a board is solvable exactly when releasing free arrows one after another
  clears it. The board maker fills the grid from the seed (integer random numbers only, as every game), checks
  this, and where it gets stuck (arrows blocking each other in a ring) turns one arrow in the ring around and
  checks again; after 50 tries it starts over from the next number of the seed. So the challenge is seeing which
  arrows are free, not finding a lucky order.
- **Modes** (`modes`, default `classic`):

  | id | label | board | arrows |
  |---|---|---|---|
  | `little` | Little 5 × 5 (gentle) | 5 × 5 | short (1–2 cells), about 10, no hearts |
  | `classic` | 8 × 8 | 8 × 8 | 1–4 cells, about 22, board ~85 % full |
  | `big` | 12 × 12 | 12 × 12 | 1–5 cells, about 45, board ~90 % full |
  | `twisty` | Twisty 10 × 10 | 10 × 10 | bent arrows of 2–6 cells, about 30 |
  | `levels` | Levels | 5 × 5 growing to 12 × 12 | numbered boards, the same for everyone: board *n* is made from the fixed seed `arrows-level-<n>`, harder as *n* grows (size, arrows, longer and bent arrows, fuller board); 200 boards. Each cleared board leads to the next; hearts carry over, +1 heart per cleared board (at most 3); `level` = the board. Carries on from the next uncleared board next time (SPEC §14) |

- **Hints** (start-screen option *Hints*, `offInRaces`, default *Off*; races always play without): 💡 Hint (`alt`, the C key, a
  controller's X) flashes one arrow that is free now; 200 points each.
- **Score** (only when cleared; `unfinished_zero`): 10,000 − 10 × seconds − 300 × mistakes − 200 × hints, at
  least 10. *Levels*: each cleared board's score added, each board its own clock; the run ends when the hearts
  run out (cleared boards keep their points) or the player stops (Pause → End). Honest-score limits: 10,000 a board
  (`per_second` and `base` as Lights Out, with the level reached).
- **Race** (§13.3): the same board from the same seed; `{"rule": "fastest", "tiebreak": "score"}` — cleared first
  wins, then fewer mistakes. Hints are off in a race.
- **Controls** (`touch`, with a cursor like Lights Out): tap an arrow; or move the highlight between arrows with the
  arrow keys, WASD or the d-pad and release with Space, Enter or a controller's A. Pause is the
  shell's.
- **Looks**: every look. Arrows drawn as a line with a clear head; in the coarse looks (Retro LCD, Pixel) one arrow
  per colour band with a big chevron head; *High contrast* shows the direction by the head's shape only, never by
  colour. The released arrow flies off with a short trail; a bump shakes it and flashes the blocker.
- **Sounds**: `release`, `bump`, `heart`, `hint`, `clear`, `win`, `lose`.
- **Saving** (`state_version` 1): the board as made, which arrows are gone, hearts, mistakes, hints, the seconds,
  the board number in *Levels*.
- **Result**: `won, cause, mode, released, mistakes, hints, boards, summary`.
- **Tests** (`tests/js/arrows-logic.test.js`): every mode's boards for many seeds are solvable by releasing free
  arrows; the same seed gives the same board on every run, with `Math.random` and the float functions made to
  throw (as wave 6); a free arrow leaves, a blocked one bumps and costs a heart, *Little* never loses one; a twisty
  arrow leaves when its head's way is clear whatever its body is; the score formula and its floor; Levels' hearts;
  saving and continuing; board *n* of *Levels* is the same on every run and starting at level *n* plays it
  (SPEC §14). Python: the `games.py` entry, its modes and limits, and that a race turns hints off.
- When built: the root README's game count (42 → 43) and its Arcade row, the DOCS game list and controls guide, and
  `index.html`'s two script tags.

## Start-screen options that stay off in races

An option may set `offInRaces: true` (the registry keeps it). The shell (`optionValues()` in play.js) then uses
the option's default in a race and in a daily challenge, so both players — or everyone on the day — play the
same game. Word Guess's **Clue at the start** (`clue`: `none` / `letter`) is the first: one letter of the answer
shown in its place before the first guess, its place drawn from the seed after the answer (so the same seed gives
the same word with or without it), the key coloured as found, Strict mode keeping it in place, and
`CLUE_COST` (500) taken off the score. A saved game keeps `clue`; older saves have none.

## Wave 10 — five more calm puzzles

The same frame as Arrow Release: boards made from the seed with integer random numbers only and checked before they
are shown; modes by size, a gentle one, and **Levels** — 200 numbered boards, board *n* made from the fixed seed
`<game>-level-<n>` (the same for everyone), harder as *n* grows, each cleared board leading to the next and the next
game starting at the first uncleared one (SPEC §14). One board's score: 10,000 − the game's costs, at least 10, only
when it is solved; in *Levels* the boards' scores add up, and the run ends when the player ends it (Pause → End
game, score kept) or after board 200. Every game: 💡 Hint (`alt`, C, a controller's X) with the start-screen option
*Hints* (`offInRaces`, default Off), saving (`state_version` 1), all six looks, a race (`{"rule": "fastest",
"tiebreak": "score"}`), `touch` controls with a keyboard / controller cursor (arrows move, Space / A acts).
Honest-score limits: `max_score` 2,000,000 (200 boards), `per_second` 10,000, `base` 10,000.

- **Car Park** (`parking`, 🚗). A 6 × 6 car park with one exit on the right of the third row. Cars 2 long and
  lorries 3 long, each lying across or along; a car only moves forwards and backwards along its length. Get the red
  car out through the exit. Drag a car (or select it with the cursor and Space, then the arrows) any number of free
  squares: one **move**. The red car leaves when it reaches the exit. Boards: cars placed from the seed, every
  position the cars can reach worked out (at most 30,000), and the position chosen whose fewest moves to free the
  red car is closest to the board's target — so the **fewest moves** is known exactly and shown. Modes: `little`
  Little (gentle, 2–5 moves), `classic` Classic (8–14), `hard` Hard (15–25), `levels` Levels (2 rising to 30).
  Hint: the next move of a fewest-moves answer from where the cars are now. Score: 10,000 − 5 × seconds − 50 × (moves
  − fewest) − 200 × hints. Undo (`undo`) takes a move back (it still counts).
- **Colour Sort** (`watersort`, 🧪). Tubes of coloured water, four layers each. Tap a tube, then another: the top
  colour pours across — every layer of it that fits — if the other tube is empty or has the same colour on top and
  room. Sort every colour into a tube of its own. Boards: the layers shuffled from the seed into the tubes, two
  empty tubes, and a depth-first search (a budget of 200,000 positions; a board it can't prove solvable is made
  again) checks there is an answer. Modes: `little` 3 colours (gentle), `classic` 7 colours, `big` 10 colours,
  `levels` 3 rising to 12. Undo; Restart (`erase`) starts the board again. Hint: a pour that still leads to an
  answer (or "Stuck — undo or restart"). Score: 10,000 − 5 × seconds − 10 × pours − 200 × hints.
- **Bolt Sort** (`bolts`, 🔩). Bolts holding nuts, four to a bolt. Move one nut at a time: the top nut onto an empty
  bolt or onto a nut of its colour with room. Fill each bolt with one colour. In *Hidden* and on higher levels the
  nuts under the top start as "?" and show their colour when they come to the top, so planning is part guesswork.
  Boards: shuffled from the seed, two empty bolts, checked solvable with every colour known. Modes: `little` 3
  colours (gentle), `classic` 6 colours, `hidden` 6 colours hidden, `levels` 3 rising to 10 (hidden from level
  40). Undo, Restart, Hint as Colour Sort. Score: 10,000 − 5 × seconds − 10 × moves − 200 × hints.
- **Dot Connect** (`connect`, 🔵). Pairs of coloured dots on a grid. Draw a line from each dot to its partner
  through the squares next to each other; lines can't cross, and drawing over a line cuts it. Join every pair and
  fill every square. Drag from a dot (or from a line's end); with the keyboard, Space on a dot or line end picks it,
  the arrows draw, Space again lets go. Boards: a path through every square made from the seed (a zig-zag bent at
  random many times), cut into pieces 3 squares or longer; the ends are the dots, so every board can be filled.
  Modes: `little` 5 × 5 (gentle: joining every pair is enough), `classic` 7 × 7, `big` 9 × 9, `levels` 5 × 5
  growing to 12 × 12. Hint: draws one pair's line from the answer. Score: 10,000 − 10 × seconds − 200 × hints.
- **Untangle** (`untangle`, 🕸️). Points joined by lines, tangled up. Drag the points until no two lines cross.
  With the keyboard, Space picks the next point and the arrows move it. Boards: points on a grid from the seed,
  lines added only where they cross no other (so an untangled drawing exists), every point with two or more lines;
  then the points are set on a circle in a shuffled order (shuffled again if nothing crosses). Crossings are
  counted with whole-number arithmetic; lines that share a point don't cross. Modes: `little` 6 points (gentle),
  `classic` 10, `big` 16, `levels` 6 rising to 30. Hint: puts one point where it was made. Score: 10,000 − 10 ×
  seconds − 20 × moves − 200 × hints.
- **Files**: `<game>-logic.js` (rules, boards, solver, score; pure) and `<game>.js` (drawing, input) for each;
  `games.py` entries with tags (puzzle, gentle, levels); `continue_levels` with `level_total` 200 for `levels`.
- **Tests** (`tests/js/wave10.test.js`): for every game and mode, many seeds give solvable boards (each game's own
  solver, and for Car Park the stated fewest moves), the same seed the same board with `Math.random` and the float
  functions made to throw; the moves' rules (a car blocked, a pour that doesn't fit, a nut onto another colour, a
  line cut by another, crossings counted); a solved board scores by the formula; board *n* of *Levels* is the same
  on every run and starting at level *n* plays it; saving and continuing.

## Starting at a level (SPEC §14)

`create(canvas, opts)` gets `opts.startLevel` (1 by default) in the modes that carry on (`continue_levels`). A
level-list game starts at that entry of its list (`levels[startLevel − 1]`); Lights Out's *Climb* and Arrow
Release's *Levels* start at that board. Score 0, and the lives, hearts and other run-long counters as at level 1.
`level()` and the result's `level` stay the real level number, so the shell and the server can tell what was
cleared.

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
- `<game>-logic.test.js` for each of the other games (including the three wave 4 games): the rules (moves, scoring, levels, modes, the end),
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

- `wave5.test.js`: the six wave 5 games register (modes, controls, saving, a race); two phones with the same seed
  (and the same level list) and the same keys and taps end identical in the default and the level-list mode; a
  different seed differs; `status()`; the rules with engine-dependent maths made to throw. They are also in
  `race.test.js`'s list of raced games and in `games.test.js`'s registry order. Each has its own
  `<game>-logic.test.js` as above (rules, levels, determinism, save/restore, every look, honest scores, a fuzz run).

- `wave6.test.js`: the six wave 6 games register (modes, controls, saving, a race, something gentle, typed); two phones with
  the same seed (and Type Rain's stage list) and the same keys, typed keys and taps end identical in two modes each; a
  different seed differs; `status()`; every mode made and played with engine-dependent maths made to throw. They are in
  `race.test.js`'s list and `games.test.js`'s registry order, and each has its own `<game>-logic.test.js` (Slide Puzzle:
  solvability against a search, solving trays through the game file; Lights Out: the fewest presses against trying every set;
  Picture Logic: the line solver, one answer counted on 5 × 5; Tile Match: every deal's own order clears it; Code Breaker:
  the feedback; Type Rain: the same words whatever the typing, runs, stages, honest scores with a perfect typist).


- `wave7.test.js`: the six wave 7 games register (modes, touch, turns, no race, options); first movers; the computer's
  moves legal and the same for a seed at every level, the levels' strength (Hard beats Medium beats Easy; Tic-tac-toe's
  Hard never loses); scores; each game's rules and taps (Checkers' taps finishing a multi-jump, the turned-round second
  phone, Reversi's pass, Dots and Boxes' cursor, Sea Battle's placing); one screen's pass-the-phone; turn by turn (the
  board from the server's moves, a move sent and not played, syncing, an end the server decided, Sea Battle from its
  view with no other fleet); saving; a fuzz run; no engine-dependent maths; every mode in all six looks and a match from
  a picture through the game file. `turns-fuzz.js` (not a test file): random games for the Python agreement test.

- `wave8.test.js`: the four wave 8 games register (modes, touch, turns / live turns, options); DiceKit on one phone
  (the computer, pass and play, filling seats), forced moves, the same game for a seed; Ludo's and Snakes and
  Ladders' rules; dice from phones (a roll sent, never rolled here; the board from the server's moves); saving, honest
  scores, every mode in every look; Chess perft, taps (castling, en passant, the promotion chooser, a refused move),
  the engine (legal, deterministic, mates in one, within budget, Hard and Medium beating Easy), the worker's move the
  same, a match from a picture; Carrom's whole-number physics bit for bit on two instances without engine-dependent
  maths, its rules (fouls, the queen, the last coin, the 300-shot end), doubles, the computer's levels, every mode in
  every look. `wave8-fuzz.js` (not a test file): random games for the Python agreement test.
- `backnav.test.js`: this app's copy of the shared back gesture — a link or notification changing the address is
  followed, not taken as Back; Back still goes home or closes a dialog.

`tests/js/helpers.js` loads the browser files into a small fake DOM (recording canvas contexts and a
hand-driven `requestAnimationFrame`); `tests/js/index.js` lets `node --test tests/js` find the tests.
