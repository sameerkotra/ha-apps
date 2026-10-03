# Changelog

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
