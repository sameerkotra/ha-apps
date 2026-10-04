# Changelog

## 1.5.2

- **Sudoku: number lines now shade whole rows, columns and boxes** instead of drawing a line through the middle:
  every cell they cover gets one even, soft shade, so the empty cells left clear stand out as the only places the
  number can go. On Retro LCD the focus cells' digits now show (light on the dark highlight).
- **Word Guess: Clue at the start** (start screen): show one letter in its place before the first guess, for 500
  points off. Races and daily challenges always start without it. The colours in the guide now match the game
  (blue with a dot, orange with a diamond, dim), and the first message fits every look.
- **Race result card** shows the puzzle summary for Sudoku and Word Guess ("Found SMALL in 3 tries of 6") and
  **Not solved — scores 0** for a puzzle that wasn't solved.

## 1.5.1

- **Solitaire is removed.** Scores and saved games from it stay in the database but no longer show anywhere.
- **Word Guess explains its clues**: before the first guess it says to type any 5-letter word, and after that a
  key under the grid shows what the colours mean (right spot, in the word, not in it).

## 1.5.0

- **Play together (a race)**: **Play with someone** on a game's start screen invites another person in the
  household to race the same game (same seed, mode and levels) on their own phone. A bar above the game shows
  their score, level and whether they're still playing; the better score wins, each score is also saved as a
  normal game, and Rematch is one tap. Works for Snake, Brick Breaker, Falling Blocks, Lane Racer, Flap, Mines,
  Merge, Colour Memory, Memory Cards, Tap the Mole, Number Dash, Sky Defenders, Rocks, Road Hop and the four new
  games.
  - Invites arrive as a Home Assistant phone notification with **Join** / **Not now**, a pop-up in the app and a
    **Waiting for you** card on Games; they last 5 minutes, can be cancelled, one out at a time.
  - Children's limits, quiet hours and allowed games apply to each player; Practice needs both to agree.
  - **My scores → Against others**: wins, losses and draws per person and game.
  - New App setting **Invites by phone notification** (on).
  - The live link uses a WebSocket, with an automatic slower fallback when a phone can't keep one open.
- **New games**: **Sudoku** (Easy, Medium, Hard, Expert; notes with Fill notes; number lines through the rows,
  columns and boxes of a number; hints that explain before filling in; mistakes shown at once or at the end;
  undo), **Solitaire** (draw one or three, every deal winnable, hint, auto and undo), **Word Guess** (six or
  eight tries, or Strict) and **Word Search** (four sizes, hints).
- **Daily challenges**: off by default; the App setting **Show daily challenges** adds Today's challenges on
  Home (the same three games for everyone each day), one ranked try a game, and a Daily leaderboard with a
  monthly days-played ranking.
- New App setting **Sudoku hints per puzzle** (3).
- Games can have start-screen options remembered per person, and word games take the keyboard.

## 1.4.4

- **No more cut-off buttons on an unfolded phone** in Tank Battle, Falling Blocks, Sky Defenders and Rocks: their
  button grids are measured as drawn (they can be wider than their box), and after fitting, the app checks that
  every button is fully on screen and shrinks the game a little if one isn't.

## 1.4.3

- **A smaller start card**: Mode and Look side by side, smaller buttons and text, so more of the game shows
  behind it; on a short game (a phone on its side) it drops its title and still fits without scrolling.
- **How to play is folded away**: the **?** button next to the sound button opens and closes it (remembered on
  each device), and the game uses the room it frees.
- **Nothing cut off on wide folding phones**: on an unfolded phone the controls beside the game keep their full
  size and the game is sized around them, so no button sits past the right edge.

## 1.4.2

- **The game fills the screen it's on.** The game is now as big as the window allows, keeping its shape: a
  folded or unfolded phone, a phone on its side, a tablet or a browser window of any size. The on-screen
  controls go under the game or beside it, whichever leaves the game bigger, and everything resizes the moment
  the screen changes (unfolding a phone, turning it, resizing the window).
- Large screens stay smooth: the game draws at a sharp but limited resolution when it's very big.

## 1.4.1

- **Tank Battle answers faster**: a quick tap on an arrow still turns and moves the tank; turning into a gap
  between walls when you're a few pixels off slides you in instead of stopping at the corner; Fire pressed while
  your shell is still flying fires the moment it's gone; your tank and its shells are a little quicker.
- **Two controllers in Snake Duel**: with two game controllers connected, the second one steers player 2.
- **Documentation**: which game controllers work (Xbox, PlayStation, Switch Pro, Bluetooth and phone-clip
  pads), how to pair them with your phone, what each button does in every game, and what to try if a
  controller isn't noticed.

## 1.4.0

- **Eleven new games**:
  - **Mines**, **Merge**, **Colour Memory**, **Memory Cards**, **Tap the Mole** and **Number Dash** (sums and
    times tables against the clock).
  - **Tank Battle**, **Sky Defenders**, **Rocks**, **Road Hop** and **Snake Duel** — two snakes on one board,
    against the computer or two people on one screen (arrows and W A S D, or each player swipes on their half).
- **Levels for every game**: each game now has a list of levels that an AI model can add to, with a mode that
  plays it — Falling Blocks *Challenges*, Paddle Duel's opponents, Lane Racer *Stages*, Flap *Courses*, Mines
  *Shaped boards*, Merge *Goals*, Colour Memory and Memory Cards *Challenges*, Tap the Mole *Gardens*, Number Dash
  *Challenges*, Tank Battle *Arenas*, Sky Defenders and Rocks *Waves*, Road Hop *Levels* and Snake Duel *Arenas*.
  Every level is checked so it can be played and keeps scores honest.
- **Admin → AI usage**: every request to the AI model — tokens today, this week, this month and all time, a
  chart by day, totals by game and model, the latest requests — with an estimated cost from prices you add on
  App settings.
- **Games page views**: a list, small squares (icon and name) or large squares (best, modes, levels, a saved
  game, how often played, how to play). Your choice is remembered on each device.
- **Falling Blocks**: a **Hold** button (C or Shift) to keep a piece for later, and new phone buttons: Hold on
  the left, ◀ ↻ ▶ with ▼ under the turn button in the middle, Drop on the right.
- **Flap** is gentler: one flap lifts the flyer a little, it falls more slowly, and the gaps don't jump as far,
  so it doesn't shoot up or drop away.
- Retro LCD and Pixel looks can now show =, ÷ and −.

## 1.3.0

- **Four new games**:
  - **Falling Blocks** — turn and drop the falling shapes to fill whole rows. *Classic*, *Fast start* (from level
    6) or *Rising floor* (rows with one gap push up from the bottom). Clearing more rows at once scores more.
  - **Paddle Duel** — your paddle against the computer's, first to 5 points. Win a match and a quicker opponent
    comes on; beat all ten to win. *Easy*, *Normal* or *Hard*.
  - **Lane Racer** — change lanes to get past the traffic on *3 lanes*, *4 lanes* or *Rush* (faster, one life).
    Every row of traffic leaves a way through; coins score extra and the road gets faster.
  - **Flap** — tap to flap through the gaps in the gates. *Easy*, *Normal* or *Moving gates*.
- All four have the six looks, sounds, Practice, bests and leaderboards, play-time limits, and **Save for later**.
- On a phone: tap and drag on the game, or use the big buttons below it (beside it when the phone is sideways).

## 1.2.0

- **Save a game and come back to it**: pause, then **Save for later**. The game waits on its start screen with its
  score, level and time; **Continue** carries on where you stopped. One saved game per game for each person; saving
  another replaces it and keeps the replaced game's score. **End it** keeps a saved game's score, **Throw away**
  drops it.
- **Scores count when you stop early**: **End game** on the pause screen (it was Quit), or leaving a game part-way,
  keeps the score so far like a finished game.
- **Admin → Levels**: move levels earlier or later (◀ ▶ or a place number) — games play them in that order — and
  delete levels the AI made. Built-in levels keep the place you give them.

## 1.1.2

- **See a look before you play**: the start screen sits over a picture of the game, and picking a look redraws it
  at once.
- **Mode is a drop-down**, like Look, on every game's start screen.
- **Admin → Levels is shorter**: each game's levels are folded away until you open them.

## 1.1.1

- **Controls in the middle**: the arrow pad, and Brick Breaker's Launch button and drag strip, are centred under the
  game (beside it on a phone held sideways). The second pause button among the controls is gone; pause with the ⏸
  button at the top, P or Esc.
- **Snappier controls**:
  - Snake turns at once when you press while it is past half-way into the next square, instead of waiting for it;
    swipes turn sooner.
  - Brick Breaker's paddle keeps up with your finger or mouse (it used to trail behind), and the arrow keys reach
    full speed faster.

## 1.1.0

- **AI levels**: Brick Breaker and Snake's Maze can get more levels from an AI model — your own Ollama, an
  OpenAI-compatible service or Anthropic Claude, set up on Admin → App settings like Finance Dashboard. Off until
  an admin turns it on.
  - Built automatically when someone nears the last level (or on request), a few at a time, with a daily limit.
  - Every level is checked by the app before anyone plays it: size, characters, difficulty, a reachable maze with a
    free start, a friendly name, no copies or mirror images. Only game rules and levels are sent to the model.
  - Optionally, new levels wait for an admin's OK.
- **Admin → Levels**: every game's levels with a small picture, who got furthest, the last build and its notes;
  Build more levels, Retire, Use again and Approve.
- **Snake · Maze**: a new mode played through ten mazes (more with AI levels); eat the foods shown to clear each one.
- Brick Breaker plays its whole level list, then repeats it faster. A game keeps the levels it started with.

## 1.0.0

First release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Two games: Snake (walls or wrap-around, slow, normal or fast) and Brick Breaker (ten levels, power-ups that can be switched off).
- Keyboard, touch (arrow pad, swipes, drag the paddle, left- or right-handed) and game controller controls; the game pauses when the app is hidden or on the back gesture.
- Six looks (Modern, Retro LCD, Neon, Pixel, Paper, High contrast) and optional sound, chosen by each person.
- Personal bests, the last 20 games, a household leaderboard (all time and this month) and Practice games that aren't saved.
- Children: daily play time for school days and weekends, quiet hours, allowed games, leaderboard visibility, extra time and play history, with school days and holidays on App settings.
- Optional Home Assistant sensors and phone notifications (new records, "5 minutes left" to parents), both off by default.
- Sign-in with your Home Assistant account, in-app App settings, and database backup and restore.
