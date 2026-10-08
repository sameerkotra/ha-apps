# Changelog

## 1.12.2

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- The Household Assistant gets a score's date in Home Assistant's zone (it was UTC's).
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 1.12.1

- **Every game with levels now carries on from the next level**. Brick Breaker, Falling Blocks · *Challenge*, Paddle
  Duel, Tank Battle, Sky Defenders · *Waves*, Rocks · *Waves*, City Defense · *Waves* and Snake Duel · *vs computer*
  used to start at level 1 every time and weren't shown under **My scores → Level progress**; now they remember the
  levels you've cleared and start at the next one (pick an earlier level from the **Level** drop-down to replay).
  Only two-player modes still start at level 1. Checked in a browser for all 44 game modes that carry on.

## 1.12.0

- **Arrow Release: more, smaller arrows, and zoom**. The boards are bigger — *14 × 14*, *20 × 20*, *Twisty 16 × 16*,
  and *Levels* growing to 24 × 24 — and packed fuller, with thin arrows that all have a tail (which blocks the others
  as much as the head does). **Pinch** to look up close, or use the mouse wheel or the new **－** / **＋** buttons;
  drag to move around while zoomed in, with a little map in the corner. A pinch or a drag never releases an arrow.
  Numbered *Levels* boards are new, so your level stays but the boards look different.

## 1.11.0

- **AI-made levels for the puzzles**: nine puzzles that made every board themselves now also have a list of named
  boards that an AI model can add to (Admin → Levels, or automatically when someone nears the end), like the other
  games with levels: Arrow Release · **Picture boards** (arrows filling a picture), the **Puzzle book** of Car Park,
  Colour Sort, Bolt Sort, Dot Connect and Untangle, Picture Logic · **Picture book**, Word Search · **Themes** and Tile
  Match · **Layouts**. Each comes with built-in boards, and every board a model writes is checked before anyone can
  play it — solved by the app where it can be (the fewest moves out of a car park, a way to sort the tubes, a heap that
  can be cleared, a picture the numbers decide). Every list carries on from your next level.
- Car Park's *fewest moves* is now always exact (it could be counted a few too many on busy boards).

## 1.10.0

- **Six new puzzles**: **Arrow Release** (tap the arrows away without bumping one into another), **Car Park** (slide
  the cars to get the red one out), **Colour Sort** (pour the colours into tubes of their own), **Bolt Sort** (move
  the nuts so each bolt holds one colour — with a *Hidden* mode), **Dot Connect** (join the pairs and fill every
  square) and **Untangle** (drag the points until no lines cross). Each has a gentle mode, bigger ones, and
  **Levels** — 200 numbered boards that get harder. Every board is checked solvable before it's shown. An optional
  **Hints** setting on the start screen (off by default, and always off in races) costs 200 points a hint.
- **Carry on from your next level**: games whose levels are puzzles or goals (the new puzzles' *Levels*, Snake's
  *Maze*, Mines' *Boards*, Merge's *Goals*, Lights Out's *Climb* and more) now remember the levels you've cleared
  and start at the next one — on any phone. A **Level** drop-down on the start screen lets you replay any cleared
  level, **My scores → Level progress** has **Start over**, and an admin can reset a person's progress on
  **Admin → Users**. Leaderboards and personal bests still count only games started from level 1.
- **Favourite games**: tap the ☆ on a game (or in a game's header) to keep it in **★ Favourites** at the top of
  the Games page. Each person has their own, on every device.
- **Search the games**: a search box on the Games page finds games as you type — by name (`tic` finds
  Tic-tac-toe), by kind (*puzzle*, *word*, *board*, *dice*, *levels*, *two players*, *turn by turn*; *kids* or
  *easy* for the gentle ones) or by mode (`7 x 7`). Press `/` to jump to it, Esc to clear it, Enter to open the only
  game left.

## 1.9.1

- **Shared AI code**: the AI connection can offer tools to a model in its own way (used by the Household Assistant); nothing changes in this app.

## 1.9.0

- **Answers the Household Assistant**: the new Household Assistant app can ask Arcade for the leaderboard ("Who has the high score in Snake?" — one game's top 10, or who holds each game's record) and for a person's own bests and play time this week, with a link back to the Leaderboard or My scores. The leaderboard is never given to a child, and nothing while it's switched off. On by default; an admin can turn it off in **App settings → Household Assistant**, and each person on **Settings → Let the Household Assistant answer for me**.
- Arcade now joins the household apps' message bus (Home Assistant's event bus). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 1.8.0

- **Phone notifications open the right page.** Tapping a notification (an invite's **Join** / **Not now**, a
  your-move notice, a new record, "5 minutes left" with **Add 15 minutes**) used to open the admin's Settings → Apps
  page — or nothing, for anyone who isn't an admin — and lost the part that said which page to show. Notifications
  now open the app's own sidebar page at the right place (the invite, the game, the leaderboard, the child's card).
  The app's page is read from the Supervisor at start-up; an app without a sidebar page sends notifications without
  a link.
- **Live duels on two phones removed**: the *Two phones* modes of Snake Duel, Paddle Duel and Tank Battle (Together /
  Against each other) and Carrom's *Two phones (live, taking turns)* are gone, with the relay behind them. They ran
  in lockstep, which stops and starts on anything slower than a quiet home network, and weren't playable enough to
  keep. What stays: Snake Duel's *Two players* and the board games' and Carrom's one-screen modes, racing, and turn
  by turn from two phones (the board games, Chess, Ludo, Snakes and Ladders). A live match from before shows as
  ended without a result.

## 1.7.2

- **Live duels (two phones) are playable on a slow link**: Snake Duel, Paddle Duel and Tank Battle on two phones used
  to stop and start — "so slow it isn't playable" — whenever the connection was slower than a home Wi-Fi hop (Home
  Assistant Cloud, the companion app away from home, a busy network): the input delay the server chose had no room
  for jitter and was capped too low, so the phones spent most frames waiting for each other. Now the starting delay
  has headroom, and each phone raises its own delay while it finds itself waiting (and lowers it again after ten
  clean seconds), so a slow link costs a little input lag instead of a stuttering game. The app also sends each
  phone's word the moment it arrives instead of looking every 10 ms, and a phone speaks once a frame. The HTTP
  fallback (no WebSocket) sends its next request the moment the last is answered.

## 1.7.1

- **Sudoku: choose how number lines shade**: **Rows, columns and boxes** (as before), **Rows and columns only**, or **None**, in the **Number lines** choice on Sudoku's start screen. If you had number lines off, you get None. It only changes what you see, never the score.

## 1.7.0

- **22 new games** (42 in all):
  - **Arcade**: Bubble Pop, Gem Swap, Tower Stack, Runner, Lander and City Defense.
  - **Puzzles**: Slide Puzzle, Lights Out, Picture Logic, Tile Match, Code Breaker and Type Rain.
  - **Board games**: Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes and Sea Battle — against the
    computer (three strengths) or another person.
  - **Classics**: Ludo, Snakes and Ladders, Carrom and Chess (all the rules; the computer thinks in the background so the
    page never freezes).
  Every new game works in all six looks, with keyboard, touch and a game controller, and can be paused, saved and
  resumed. The arcade and puzzle games can be raced with someone (Play with someone).
- **Play live (two phones)**: Snake Duel, Paddle Duel and Tank Battle can now be played against another person on
  their own phone, in step, as one game. Carrom is played live too, taking turns at the board. If a phone goes quiet
  both games pause; after a minute without it, the other player wins.
- **Play turn by turn (phones)**: the board games and Chess against another person, and Ludo and Snakes and
  Ladders with up to three others, one move at a time, over minutes or days. The app checks every move, rolls the dice itself and
  keeps Sea Battle's ships hidden from the other player. Several games can run at once; they are listed under
  **Your games** on the Games page.
- **Your-move notifications**: a phone notification when it is your move in a turn-by-turn game (at most one every
  15 minutes per game, none during a child's quiet hours). An admin can switch it off in Admin → App settings
  (**Your-move notifications**); each person's **Receive notifications** on Settings applies too.
- Children's limits apply to every new way of playing: a game that isn't one of theirs, quiet hours or no play time
  left stop a child starting or moving, and a live match ends as a draw if their time runs out.
- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back.

## 1.6.1

- **Security**: backups no longer include the AI access key, and restoring a backup keeps the key this install already has. After restoring on a new install, enter the key again in Admin → App settings.
- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 1.6.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Ink is now Midnight; your saved choice carries over. Game looks (Retro LCD, Neon, Pixel…) are unchanged.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

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
