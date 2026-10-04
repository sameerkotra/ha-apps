# Household Arcade

> 🚧 **Under development.** Household Arcade is still changing from one version to the next. Some games
> may need tuning; tell us what feels wrong.

Household Arcade is a small collection of classic arcade games for everyone in
a Home Assistant home. It opens from the Home Assistant sidebar on a computer
and in the Companion app on a phone. There is no separate login: everyone plays
as themselves, recognised by their Home Assistant account, and the household
gets personal bests and a shared leaderboard. Admins can mark children and give
them play-time limits.

The games run in your browser, drawn in code. They are written from scratch for
this app: there are no ROMs, no emulators and no downloads, and the app never
contacts the internet.

## Getting started

1. In Home Assistant, go to **Settings → Apps → Install app**, open the
   **⋮** menu (top right) → **Repositories**, paste
   `https://github.com/sameerkotra/ha-apps` and select **Add**.
2. Still in **Settings → Apps → Install app**, find **Household Arcade** and
   **Install** it, then **Start** it on its **Information** tab. Turn on
   **Show in sidebar** there too; the sidebar entry is called **Arcade**.
3. Open it from the sidebar. Until an admin is set, every page shows a
   **"No admin yet"** banner with your Home Assistant user name in it.
4. In the app's **Configuration** tab, add that user name to
   **`admin_users`**, **Save**, and restart the app (**Information** tab →
   **Restart**). Your user name is also shown under **Settings → How the app
   sees you** in the app.
5. Back in the app you now have **🛡️ Admin** in the sidebar. First steps:
   - **Admin → App settings**: pick the games that are on, the default look,
     and whether to use the Home Assistant sensors and notifications.
   - **Admin → Users**: mark children and set their limits. People appear
     here after they have opened the app once.

Only `admin_users` is set in the Configuration tab; everything else is in the
app and changes apply straight away, without a restart.

## Playing

**Games** (the start page) shows every game that is switched on. The three
buttons at the top right change how: **☰ List** (a row each, with your best),
**▦ Small squares** (just the icon and name — the quickest to scan) and **◼ Large
squares** (everything: your best, the modes, how many levels the game has, a
saved game, how often you've played and when, and how to play). Your choice is
remembered on that device. Pick a game to open its start screen, which sits over
a still picture of the game:

- **Mode** (a drop-down) — Snake: *Walls* (hitting the edge ends the game) or *Wrap* (go out
  one side, come back in the other), each at *Slow*, *Normal* or *Fast*, or
  *Maze* (a series of mazes, below).
  Brick Breaker: *Power-ups* or *Classic*. Falling Blocks: *Classic*, *Fast
  start* or *Rising floor*. Paddle Duel: *Easy*, *Normal* or *Hard*. Lane
  Racer: *3 lanes*, *4 lanes* or *Rush*. Flap: *Easy*, *Normal* or *Moving
  gates*. Most games also have a mode that plays a **list of levels** (below:
  *Challenges*, *Stages*, *Courses*, *Waves*, *Gardens* …); with AI levels on,
  the list keeps growing. Every mode has its own bests and its own leaderboard.
- **Look** (a drop-down) — how the game is drawn (see *Looks*). The picture
  behind changes as soon as you pick one, so you can see it before playing.
  Your choice is remembered.
- **Practice** — play without saving the score.
- **Play** — starts the game. For a child, the start screen also shows the
  time left today.

### Snake

The snake moves on its own; steer it to the food. Each piece of food makes it
one longer and scores 10 points × the speed (Slow 1, Normal 1.5, Fast 2); it
speeds up a little every 5 pieces. Bonus food appears now and then for a few
seconds and is worth 50. The game ends when the snake runs into itself (or a
wall, in Walls mode). Two quick turns are both kept, so tight turns work; a
turn straight back is ignored.

**Maze** is played maze by maze: each has walls inside the board and a number
of foods to eat (shown at the bottom, for example *Two bars · 3/6*). Eat them
all to clear the maze — that scores 50 × the maze's number — and the next maze
starts, a little faster, with the snake back at the start. There are ten mazes
to begin with; with AI levels on, more are added as people get near the end.
Clearing the last maze there is wins the game.

### Brick Breaker

Keep the ball in play with the paddle and clear every brick to finish the
level. Where the ball hits the paddle sets its angle: the edges send it out
wider. Some bricks need two or three hits (they are drawn with a pattern, not
just a colour). You have three lives. There are ten levels to begin with
(more with AI levels on), and when you clear the last one they repeat faster. In *Power-ups* mode, broken bricks sometimes drop a wider
paddle, a slower ball, an extra ball or an extra life; catch it with the
paddle. Bricks score 10, 20 or 30 by toughness, and each cleared level scores
100 × the level number.

### Falling Blocks

Shapes of four squares fall into a well 10 wide. Move and turn each one as it
falls; when a row is full it clears. Clearing 1, 2, 3 or 4 rows at once scores
100, 300, 500 or 800 × the level; dropping a piece scores a little too. Every
10 rows is a new level and the pieces fall faster. The next three pieces are
shown on the right, and a faint outline shows where the falling one will land.
*Fast start* begins at level 6. In *Rising floor* a row with one gap pushes up
from the bottom every few seconds (sooner at higher levels). The game ends
when the pieces reach the top. **Hold** (C or Shift, or the Hold button) puts
the falling piece aside for later — or swaps it with the one you put aside —
once per piece. **Challenges**: each challenge starts with some rows already
in the well and a number of rows to clear, at its own speed; clear them and the
next challenge starts on a fresh well (your held piece comes with you). Finish
the last one to win.

### Paddle Duel

Your paddle is at the bottom, the computer's at the top. Get the ball past the
computer to win a point; first to 5 wins the match. Where the ball hits your
paddle sets its angle — edge shots are harder to return. Each return scores 10,
each point 100 × the match number and each match won 1,000 × the match number.
Win a match and the next opponent is quicker; lose one and the game is over;
beat the last one and you've won. Every mode plays the list of opponents, each
with its own speed, aim, serve and points to win (ten to start with; Easy,
Normal and Hard make every opponent a little slower or quicker). The ball is
served after a moment (Space or a tap serves at once).

### Lane Racer

Change lanes to get past the traffic coming toward you. Every row of cars
leaves a way through that one lane change reaches, so a crash can always be
avoided. You score 1 for every bit of road, 25 for each coin, and the road gets
faster level by level. A crash costs one of your three lives (*Rush* starts
faster and has one); after a crash you can't crash again for a moment.
**Stages**: each stage is a stretch of road with its own lanes, speed and
traffic, ending at a checkered finish line; three lives for the whole run;
cross the last finish line to win.

### Flap

Your little flyer falls unless you flap. Fly through the gaps in the gates:
each gate is a point, and every 10 gates the gaps get a little narrower and the
gates a little quicker. Touching a gate or the ground ends the game. The game
waits for your first flap. One flap lifts the flyer a little; tap again to climb
more. In *Moving gates* the gaps sway up and down. **Courses**: each course is a
run of gates with its own gaps, speed and pattern of heights; finish it for the
next course, and the last one to win.

### Mines

Open squares without hitting a hidden mine. Your first square is always safe, and so is
everything around it. A number tells you how many mines touch that square. To mark a mine,
hold your finger on it or switch on 🚩 Flag and tap; the top right shows how many mines are left.
Tapping a number whose mines are all flagged opens the squares around it. Clear every safe
square for a bonus (bigger the faster you were) and a new board with a few more mines.
Opening a mine ends the game. **Shaped boards** plays a list of picture boards — a heart, a
ring, stairs, a tall tree — each with its own number of mines; clear the last one to win.

### Merge

Slide all the tiles up, down, left or right. Two tiles with the same number that run into each
other join into one worth double, and that number is added to your score. A new 2 or 4
appears after every move. Your level is how big your best tile is; keep going after the
big tile. The game ends when the board is full and nothing can join. **Goals** gives you a
tile to make on its own board — sometimes small, sometimes with hatched stones that never
move and stop tiles sliding past; make it for the next goal, and the last one to win.

### Colour Memory

Four coloured pads light up one after another; then it's your turn to repeat
the order. Each round adds one more pad, and the playback gets quicker as you go
(*Fast* starts quicker). In *Backwards* you repeat the order from last to first.
A wrong pad, or five seconds without pressing one, ends the game, and the pad
you needed is ringed. Each round you get right scores 10 for every pad in it.
**Challenges**: repeat the sequence until it reaches the length shown to clear
a challenge; each has its own speed and direction.

### Memory Cards

Turn over two cards at a time to find the pairs. A pair stays face up; two that
don't match turn back after a moment (or straight away when you tap another
card). Each pair scores 20 times the number of pairs you've found in a row. Clear
the board before the time bar runs out for a bonus (more for time left and few
misses) and a new board with a little less time. When the time runs out, the game
ends. **Challenges**: boards of different sizes and times; some show every card
for a few seconds first, some use fewer kinds of picture.

### Tap the Mole

Friendly moles pop out of the holes and duck back down after a moment. Tap one
while it's up to bonk it for 10 points; a golden mole with a crown is worth 50
but doesn't stay up long. From level 3 a spiky hedgehog sometimes pops up
instead: leave it alone, because tapping it costs a life. Every 10 bonks the
moles come quicker. In *Classic* (9 holes) and *Big garden* (16) you have three
lives and every mole that gets away costs one; *60 seconds* has no lives, but a
tapped hedgehog takes 5 seconds off. **Gardens**: gardens of different shapes,
each with a number of moles to bonk; three lives for the whole trip.

### Number Dash

A sum appears at the top with four answers in a diamond. Pick the right one
(tap it, or press the arrow that points to it) before the clock runs out. Each
right answer adds a second, each wrong one takes three off and shows the right
answer. Answer 5 in a row for double points and 10 in a row for triple. Every 8
right answers the numbers get bigger. *Times tables* works up to 12 × 12;
*Mixed* has everything. **Challenges**: short tasks with their own sums, a
number of right answers to get and a fresh clock.

### Tank Battle

Drive your tank and protect the flag beside you at the bottom. Enemy tanks come
in from the top; destroy them all to clear the arena and move on. Each tank has
one shell in the air at a time. Brick walls break, steel doesn't; water stops
tanks but shells fly over it, and tanks can hide in bushes. A hit costs a life
(3, or 5 in *Easy*) and you come back with a short shield. The game ends with no
lives left or when a shell hits the flag. Every mode plays the list of arenas;
clearing the last one wins.

### Sky Defenders

A marching formation of little critters comes down from the sky. Move your
cannon along the bottom and shoot them one shot at a time — the small ones at
the top are worth 30, the middle ones 20, the big ones 10. They step down at
each edge and speed up as fewer are left, and they drop bombs: hide behind the
shields, which wear away bit by bit. A bomb costs one of your 3 cannons; if the
formation reaches the ground the game is over. A bonus ship sometimes flies
across the top. **Waves** plays the list of waves; the last one wins.

### Rocks

Steer a small ship through drifting rocks: turn, thrust (the ship slows
gently) and fire short shots. A big rock breaks into two medium ones, a medium
rock into two small ones. Clear every rock for the next wave. The edges wrap
around. From wave 3 a saucer sometimes flies across and shoots at you. Bumping
into anything costs a life (3, 5 in *Calm*), and a new ship gets a moment of
safety. **Waves** plays the list of waves; the last one wins.

### Road Hop

Help a little hopper get home. Each press, swipe or tap hops one square: first
across the road — cars and trucks squash you — then across the river, riding
the logs and shell rafts (the water swallows you, and don't ride off the edge).
Hop into an empty home at the top before the time bar runs out: 10 points a new
row, 50 a home plus the seconds left. Fill all five homes for the next level.
You have 3 hoppers. **Levels** plays the list of levels; the last one wins.

### Snake Duel

Two snakes share one board, and each tries to make the other crash into a wall,
itself or the other snake. If the heads meet, both crash and nobody wins the
round. Eating food makes your snake longer. The first to win 3 rounds wins the
match. *Against the computer*: win a match to move on to the next arena —
faster, more walls, a smarter opponent; lose one and the game is over; win the
last arena to win the game. *Two players*: two people share one screen or
keyboard for one match. The score is player 1's (green, the person signed in).

### Sudoku

Fill the 9 × 9 grid so every row, column and 3 × 3 box holds 1 to 9 once. Tap a square, then a
number (or type it). **✎ Notes** switches to pencil marks; **Erase** clears a square; **Undo** takes back a move; **Hint**
fills one square (3 a puzzle, an admin can change that; unlimited in Practice). **Easy** to **Expert**
get fewer clues. The start screen has **Show mistakes** (a wrong number turns red at once — it
adds 10 seconds to your time) and **Number lines** (highlights the row and column). Your score is
10,000 minus your time in seconds, with 30 seconds added for each hint, so the fastest finish ranks first.
A puzzle you didn't finish scores nothing and isn't kept. A game can be saved and carried on later.

### Solitaire

Classic Klondike: build the four suit piles from ace to king, and stack cards down in
alternating colours in the seven columns. Tap a card to send it where it fits, or drag it. **Draw one** or
**Draw three** from the stock. **Hint** points at a move, **Auto** sends every card that can go up to the
piles, and **Undo** takes a move back. Every deal can be won, in Practice too. Points come from cards sent
up, cards turned over, and a bonus for a quick win; **End game** keeps what you have so far.

### Word Guess

Find the hidden word. Type a guess (or tap the on-screen keys) and press Enter: green is the right
letter in the right place, yellow is in the word but elsewhere, grey is not in the word. **Six tries**
(5 letters), **Eight tries**, or **Strict**, where every clue you've been given must be used in the next guess.
Only real words are accepted. Score: 1,000 for each try left (plus one) and up to 999 for speed,
so fewer guesses rank first and then the faster time. A word you didn't find scores nothing.

### Word Search

Find the hidden words in the grid by dragging across them (they run in straight lines, any
direction except on **Little ones**, where they only run right and down). **Little ones**
(7 × 7, 5 words), **Kids** (9 × 9), **Everyone** (11 × 11) and **Puzzler** (13 × 13, 12 words).
**Hint** shows one word to look for and costs 25 points. Each word is worth 100, with a bonus for
finishing quickly.

### Daily challenges

Off unless an admin turns on **Show daily challenges** in *Admin → App settings*. Then each day the
app picks three games — the same seeded puzzle for everyone — and Home shows **Today's challenges**. You get one
ranked try at each (starting it counts, finished or not); afterwards you can replay it as Practice, which isn't
saved. Each challenge has its own leaderboard for the day (**Scores → Daily**) and there is a "days played this
month" ranking. Children's limits and allowed games apply. Turning the setting off hides everything and keeps
the scores.

### Controls

| | Keyboard | Phone or tablet |
|---|---|---|
| Snake | Arrow keys or W A S D | The on-screen arrow pad, or swipe on the game |
| Brick Breaker | ← → (or A D) to move, Space to launch | Drag on the game or on the strip below it; **Launch** |
| Falling Blocks | ← → move, ↑ turn, ↓ faster, Space drop, C or Shift hold | Drag the piece sideways, tap to turn, drag down to drop faster, flick down to drop; or the buttons: **Hold** on the left, ◀ ↻ ▶ with ▼ under ↻ in the middle, **Drop** on the right |
| Paddle Duel | ← → (or A D) to move, Space to serve | Drag on the game or on the strip below it; **Serve** |
| Lane Racer | ← → (or A D) | Tap the left or right half of the game, or the ◀ ▶ buttons |
| Flap | Space or ↑ | Tap anywhere on the game, or **Flap** |
| Mines | Arrows move the cursor, Space opens, C or Shift flags | Tap to open, hold to flag, or turn on **🚩 Flag** and tap |
| Merge | Arrow keys (or W A S D) | Swipe on the game, or the arrow pad |
| Colour Memory | ↑ ← → ↓ (or W A S D) | Tap the pads |
| Memory Cards | Arrows move the ring, Space turns a card | Tap a card |
| Tap the Mole | Arrows move the ring, Space bonks | Tap a mole |
| Number Dash | ↑ ← → ↓ pick the answer in that direction | Tap an answer |
| Tank Battle | Arrow keys drive, Space fires | The arrow buttons and **Fire** |
| Sky Defenders | ← → move, Space fires (hold to keep firing) | Drag on the game to move, tap to fire, or ◀ ▶ and **Fire** |
| Rocks | ← → turn, ↑ thrust, Space fires | ⟲ ⟳ ▲ and **Fire**; a tap on the game fires |
| Road Hop | Arrow keys (or W A S D) hop, Space hops forward | Swipe, tap to hop forward, or the arrow pad |
| Snake Duel | Green: arrow keys; blue: W A S D | Swipe on the right half for green, the left half for blue |
| Pause / resume | P or Esc | The ⏸ button at the top |

The arrow keys and Space only belong to the game while a game is on screen, so
they never scroll Home Assistant's page. A game controller works too — see
*Playing with a game controller* below.

On a phone the on-screen buttons sit below the game when the phone is upright
and beside it when it's turned sideways. **Settings → On-screen controls**
puts them on the left or the right.

### Playing with a game controller (Xbox and others)

Most game controllers work — nothing needs setting up in the app:

- **Xbox** controllers (Xbox One with Bluetooth, Xbox Series, Elite);
- **PlayStation** controllers (DualShock 4, DualSense);
- **Nintendo Switch Pro** controller;
- **Bluetooth game pads** such as 8BitDo, GameSir, PowerA, SteelSeries, Logitech;
- **phone-clip controllers** that hold the phone between two grips (Backbone,
  Razer Kishi, GameSir and similar), over USB-C or Lightning;
- **USB controllers** plugged into a computer, or into an Android phone with a
  USB-C adapter.

What matters is that the phone or computer sees it as a *game controller*. A
pad in "keyboard" mode works too, as long as its d-pad sends the arrow keys.
**Pairing an Xbox controller by Bluetooth** (the controllers with the Xbox
button set into the front plastic, sold since 2016, have Bluetooth; the older
ones without it need a USB cable to a computer):

1. Turn the controller on with the Xbox button.
2. Hold the small **pair** button on top of the controller (next to the USB
   port) for 3 seconds, until the Xbox button flashes quickly.
3. On the phone open **Settings → Bluetooth** (Android: *Connected devices →
   Pair new device*) and pick **Xbox Wireless Controller**. On an iPhone or
   iPad it's under **Settings → Bluetooth → Other devices**.
4. Open the Arcade, start a game, and **press any button once** — browsers only
   let a page see a controller after a button has been pressed there.

To use it with another phone or a computer later, pair it again there (the
controller connects to the last device it was paired with).

**Pairing other controllers** — put the controller in pairing mode, then pick
it in the phone's Bluetooth settings as above:

| Controller | Pairing mode |
|---|---|
| PlayStation DualShock 4 | Hold **Share** and the **PS** button until the light bar flashes |
| PlayStation DualSense | Hold **Create** and the **PS** button until the light around the touch pad flashes |
| Switch Pro controller | Hold the small **sync** button on top until the lights run |
| 8BitDo, GameSir and other pads | Usually: switch to *Xbox / X-input* (or *Android*) mode, then hold the pair button; see the pad's manual |
| Phone-clip controllers | Nothing to pair: plug the phone in |

If a pad has modes, pick its **Xbox / X-input** or **Android** mode: then the
buttons below are in the right places.

**The buttons** — the app goes by where a button is, so every controller works
the same way:

| Button | Xbox | PlayStation | Switch Pro | Does |
|---|---|---|---|---|
| D-pad or left stick | | | | Move, steer, turn, pick (the same as the arrow keys) |
| Bottom or right face button | **A** / **B** | **✕** / **○** | **B** / **A** | Fire, launch, serve, flap, drop, open, bonk — the game's main button (the same as Space) |
| Left or top face button | **X** / **Y** | **□** / **△** | **Y** / **X** | The second button: **Hold** in Falling Blocks, **Flag** in Mines (the same as C or Shift) |
| Right-hand small button | **≡** Menu | **Options** | **+** | Pause and resume |

The shoulder buttons, triggers and right stick aren't used.

Game by game:

| Game | D-pad / stick | Main button (A) | Second button (X) |
|---|---|---|---|
| Snake, Merge, Road Hop | Turn or hop (Road Hop: the main button hops forward) | — | — |
| Brick Breaker, Paddle Duel | Move the paddle | Launch / serve | — |
| Falling Blocks | ← → move, ↑ turn, ↓ drop faster | Drop | Hold |
| Lane Racer | ← → change lanes | — | — |
| Flap | ↑ flaps | Flap | — |
| Mines | Move the ring | Open | Flag |
| Colour Memory, Number Dash | Press the pad / pick the answer in that direction | — | — |
| Memory Cards, Tap the Mole | Move the ring | Turn the card / bonk | — |
| Tank Battle | Drive | Fire | — |
| Sky Defenders | ← → move | Fire (hold to keep firing) | — |
| Rocks | ← → turn, ↑ thrust | Fire | — |
| Snake Duel | Steer your snake | — | — |

**Two controllers**: in Snake Duel (*Two players*) the controller connected
first steers green (player 1) and the second one blue (player 2). In every other
game any connected controller plays.

**If the controller isn't noticed**

- Press a button after the game page is open (pressing one before doesn't
  count), and check the controller is connected in the phone's Bluetooth
  settings.
- Browsers only allow controllers on a secure address (one starting with
  **https://**). If you reach Home Assistant at home with an `http://`
  address, use its https address instead — for example your Home Assistant
  Cloud (Nabu Casa) address, or your own https address.
- If it works in the phone's browser (Chrome, Safari) but not in the Home
  Assistant app, open the Arcade in the browser instead: the same address and
  the same login.
- On the phone's screen the on-screen buttons stay, so you can mix them with
  the controller.

### Pausing

A game pauses when you press the pause button, P or Esc, when you switch to
another app or tab or the screen turns off, and on the phone's back gesture.
After that, Back leaves the game, and Back once more leaves the app. A game
never keeps running in the background, and paused time is never counted.

While paused you can:

- **Resume**.
- **Save for later** — put the game aside and come back to it: it waits on
  the game's start screen (**Continue**), with its score, level and time so
  far. You can keep one saved game per game (one Snake, one Brick Breaker, …);
  saving another replaces it, and the one replaced keeps its score as if you
  had ended it there. On the start screen, **End it** keeps a saved game's
  score; **Throw away** drops it.
- **End game** — the game stops and its score counts, just like a finished
  game.

Leaving a game in the middle (Back, another page, closing the app) also ends
it and keeps its score. Practice games are never saved to the scores either
way. A child's limits apply to continuing a saved game too.

### Playing together (a race)

Two people in the household can race: the **same game, the same start** (the same traffic, the same pieces, the
same sums), at the same moment, each on their own phone or computer. The better score wins.

1. On a game's start screen choose the mode (and **Practice** if you both want nothing saved), then tap
   **Play with someone**. The list shows everyone who has opened the app; people who can't play right now are
   greyed out with the reason (switched off, a child's quiet hours or no play time left, a game that isn't one of
   their games, or already in a match).
2. Pick a person. They get a phone notification ("Asha challenges you to Lane Racer", with **Join** and
   **Not now**), a pop-up if they have the app open, and a card on the **Games** page under **Waiting for you**.
   An invite lasts 5 minutes; you can **Cancel** it, and you can have only one invite out at a time.
3. When they join, both phones count in 3, 2, 1 and play. A bar above the game shows the other player's score,
   level and whether they are still playing (a green dot: the phones are connected). When one of you is done
   first, you keep playing; the bar shows "finished in 6:12" and the result card waits for the other.
4. When both have finished you see both results side by side and who won (or a draw). Each score is also saved
   as a normal game for each of you (unless it was Practice). **Rematch** sends a new invite the other can accept
   with one tap.
5. **My scores → Against others** lists each person you've raced with: games, won, lost and drawn, and the same
   for each game (Practice races aren't counted).

Good to know:

- The phones talk through the app and Home Assistant only; nothing goes to any outside service. At home and
  away (for example through Home Assistant Cloud) it works the same. The link uses a WebSocket and falls back
  by itself to checking in every moment if the WebSocket can't open on your network. The bar's green dot has
  the link's kind as its tooltip.
- Each person's own limits apply: a child needs play time left and no quiet hours to start or join a race, and
  their time counts as for any game. A race you leave counts as lost; if the other phone goes away for two
  minutes while you finish, you win.
- A race can't be saved for later. Pausing pauses only your own game (the other player sees "paused").
- Racing is offered for Snake, Brick Breaker, Falling Blocks, Lane Racer, Flap, Mines, Merge, Colour Memory,
  Memory Cards, Tap the Mole, Number Dash, Sky Defenders, Rocks and Road Hop. The two-player games are for one
  screen (live play on two phones is planned).
- An admin can turn the phone notification off with **Invites by phone notification** in **App settings**;
  everyone can also turn off **Receive notifications** on Settings. The pop-up and the Games page card stay.

### Looks and sound

Each person picks their own look on the start screen or on **Settings**; an
admin sets the default for everyone who hasn't chosen.

- **Modern** — follows the app's light or dark theme, soft colours.
- **Retro LCD** — a grey-green screen with square dark "pixels", like an old
  handheld game.
- **Neon** — glowing outlines on black, like an arcade cabinet.
- **Pixel** — chunky 8-bit blocks and a small palette.
- **Paper** — pencil lines on paper, calm colours.
- **High contrast** — black, white and a few strong colours, thick outlines.

Looks change only how things are drawn, never the rules, so scores stay
comparable. **Reduce motion** on **Settings** turns off glow pulses, trails
and screen shake (your device's own reduce-motion setting is followed too).

Sound effects are made by the browser and are **off** until you turn them on
with the 🔇/🔊 button on the game page; your choice is remembered.

## Scores and the leaderboard

- Every finished game is saved with its mode, score, level and how long it
  lasted. **Practice** games and games shorter than 3 seconds aren't saved.
- **My scores** shows your best per game and mode, your last 20 games and how
  long you played this week, and **Against others** (your wins, losses and
  draws against each person you've raced). You can delete your own scores there.
- **Leaderboard** shows the top 10 for each game and mode, of **All time** or
  **This month**; each person's best appears only once. Admins can delete any
  score from it (a misclick, or a suspiciously high one) and the leaderboard
  updates at once.
- After a game you see **New personal best** or **New household record** when
  you beat one.
- The app turns away scores that are impossible for the game and the time
  played (for example more points per second than the fastest possible play).
  Scores come from the browser, so a determined person could still fake one;
  in a household that's fine.
- People switched off on **Admin → Users** can't play and don't appear on the
  leaderboard (their scores are kept).

## Settings (everyone)

- **Look**, **Sound**, **On-screen controls** (right- or left-handed),
  **Reduce motion** and **Receive notifications** (new household records,
  when an admin has switched them on).
- **Your limits** — for a child: today's time left, daily minutes, quiet hours
  and games, read-only.
- **How the app sees you** — the user name and id Home Assistant sent, and
  whether you are an admin in this app.
- **Theme** (Ink, Slate, Daylight) — in the sidebar on a computer, on Settings
  on a phone.

## Children and limits

On **Admin → Users**, an admin switches **Child** on for a person and sets
their limits on the same card. Admins can't be marked as children, and limits
never apply to anyone who isn't marked.

- **Minutes a day** — one number for school days and one for weekends and
  holidays. Leave it empty for no limit. Time counts only while a game is
  actually running: paused time, the start screen and the menus don't count.
  Five minutes before the end the child sees a warning. When the time is up,
  the game being played finishes normally; after that no new game can start
  until tomorrow.
- **Quiet hours** — from–to times for school nights and for weekends, when no
  game can be started. Times like 21:00 – 07:00 run past midnight. The
  school-night times apply on the nights before a school day (Sunday to
  Thursday with the default school days); the weekend times on the others. A
  game already running when quiet hours begin finishes, then the app shows
  "Time for a break".
- **Games** — all games, or only the ones ticked.
- **Leaderboard** — shown with full names, with first names only, or hidden
  for that child.
- **Extra time today** — **Add 15 / 30 / 60 minutes** for today only.
- **Play history** — what the child played, when and for how long (Practice
  included), over the last 14 days.

**School days** (Monday to Friday unless you change them) and **Holidays**
(dates when weekend limits apply — add a single day or a range, like a school
break) are on **Admin → App settings**. Days and times follow Home
Assistant's time zone.

The child sees the time left on **Games** and on each start screen, and their
limits on **Settings**.

If **Limit warnings to parents** is on, the chosen admins get a phone
notification when a child has 5 minutes left (once a day per child). Its
**Add 15 minutes** button opens the app on that child's card. Admins also see
"Nearly out of play time" with an **Add 15 minutes** button on **Games**.

## Admin (admins only)

### App settings

| Setting | Default | What it does |
|---|---|---|
| Games | all on | Turn a game off to hide it; its scores are kept. |
| Brick Breaker power-ups | on | Off: only Classic play. |
| Default look | Modern | For people who haven't picked their own. |
| Leaderboard | on | Off: only personal bests are shown. |
| Keep scores for | forever | Or 1, 2 or 5 years. Older games are removed, but each person's best per game and mode is always kept. |
| School days | Mon–Fri | Days that use school-day limits for children. |
| Holidays | none | Dates when children get weekend limits. |
| Limit warnings to parents | off | Phone notice to the ticked admins (every admin if none is ticked). |
| Notify new records | off | Tell the household when someone sets a new record. |
| Invites by phone notification | on | A phone notification (with Join and Not now) when someone invites you to play together. The invite also shows in the app. |
| Show daily challenges | off | Today's challenges on Home, a try on each game's start screen and a Daily tab on the leaderboard. |
| Sudoku hints per puzzle | 3 | 0 to 20; Practice has no limit. |
| Home Assistant sensors | off | See below. |
| AI levels | off | More levels made by an AI model. See *AI levels* below. |

### AI levels

Every game has a list of levels — Brick Breaker's layouts, Snake's mazes,
Flap's courses, Tank Battle's arenas, Number Dash's challenges and so on — that
starts with built-in levels. With **AI levels** on, an AI model makes more, so
the games keep going:

- **Provider, Address, Model, Access key** — the same settings as Finance
  Dashboard: *Ollama* on your network (enter its address, for example
  `http://192.168.1.10:11434`, and a model you have pulled), an
  *OpenAI-compatible* service, or *Anthropic Claude* (needs an access key). The
  key is never shown again once saved; **Remove key** deletes it. **Test
  connection** lists the models the provider offers and asks the model a tiny
  question.
- **Build ahead automatically** (on) — when someone reaches a level close to
  the last one (**Start building this many levels before the end**, 2), the
  next **Levels per build** (5) are made in the background, so nobody waits.
  Nothing is built while nobody is near the end.
- **Most levels built a day** (20) — protects a paid key; 0 means no limit.
- **Check new levels before they're played** (off) — new levels wait on
  **Admin → Levels** until an admin approves them.
- **Price per million input / output tokens** (0) — only for the cost
  estimate on **Admin → AI usage**; copy them from your provider's price list
  (leave 0 for your own model).

Only the game's rules, the level format, a target difficulty and the most
recent levels are sent to the model — never anything about the people here.
Every level the model returns is checked by the app before anyone can play it:
every number in its range, the right size and characters, playable (every part
of a maze or arena reachable, a way through every row of traffic, gates a
flyer can climb to, a mine count that fits the board …), a friendly name, and
not a copy of another level. The ranges also keep scores honest. Anything that
fails is turned down, and the model is asked again. A level is only ever data
(numbers and grids), so it can't run anything.

### AI usage

**Admin → AI usage** shows every request the app has sent to the AI model —
level builds and connection tests: tokens used and levels made today, in the
last 7 and 30 days and all time (and an estimated cost when prices are set on
App settings), a chart of tokens a day (7, 30 or 90 days), totals by game and by
model, and the latest requests with how long they took and anything that went
wrong. Only counts are kept, never the prompts or answers; entries older than
about a year are removed.

### Levels

**Admin → Levels** shows each game's levels in order with a small picture
(folded away at first: open **Levels (n)** under a game to see them),
who got furthest, and the last build (how many were made and turned down, the
model's tokens, and notes on anything turned down). **Build more levels**
makes up to 20 now. **◀ ▶** and the place number move a level earlier or
later in the list (games play the levels in this order). **Retire** skips a
level from the next game on (its scores stay); **Use again** brings it back;
**Approve** lets a waiting level be played; **Delete** removes a level the AI
made for good (built-in levels can only be retired). A game in progress never changes: new levels start with the next
game.

### Users

Everyone who has opened the app at least once, with their phones from Home
Assistant. Per person: **Can play** (switch someone off), **Child** and their
limits, extra notify services (a phone is picked up automatically from
**Settings → People → (person) → Track device** in Home Assistant; add a
`notify.…` service here for anything else) and **Send test**.

### Storage

**Download database** saves a complete backup (a `.db` file). **Import
database** replaces everything in the app with a backup; a backup from an
older version is brought up to date automatically. Home Assistant's own
backups include the app's data too.

### The app option (Configuration tab)

`admin_users` — the Home Assistant login names or user ids of the admins (not
case-sensitive; display names are never matched). Empty by default, so nobody
is an admin until you add someone. Restart the app after changing it.

## Home Assistant sensors and notifications

Both are off on a new install; an admin turns each on in **App settings**.

**Home Assistant sensors** keeps these up to date:

- `sensor.household_arcade_<game>_record` (`snake`, `brick`) — the household
  record score, with who set it, when, and the mode as attributes (plus every
  mode's record).
- `sensor.household_arcade_<person>_played_today` — minutes played today; for
  a child also `minutes_left`, `limit_minutes`, `extra_minutes` and
  `quiet_hours`.
- `binary_sensor.household_arcade_<person>_playing` — on while that person is
  in a game.

`<person>` is the person's Home Assistant user name. Use them on dashboards or
in automations: flash a light when a record falls, or remind someone when a
child's time is nearly up. Turning the switch off marks them unavailable.

**Notify new records** sends "Asha set a new Snake record: 1,240" to everyone
else who hasn't turned off **Receive notifications** on their Settings.

**Invites by phone notification** (on by default) sends "Asha challenges you to Lane Racer" to the person
invited to play together, with **Join** and **Not now** buttons that open the app (when the app knows its panel
path in Home Assistant). It follows the person's **Receive notifications** choice too.

Notifications go to each person's phone from Home Assistant's People page,
plus any extra services an admin adds on **Admin → Users**.

## Who can see what, and where your data goes

- Everything is stored in the app's own database in Home Assistant
  (`/data/arcade.db`). Nothing is sent anywhere else, and the app makes no
  requests to the internet — unless an admin turns on AI levels with a service
  outside your network, which is then sent game rules and levels only.
- Everyone sees the leaderboard (unless it's switched off, or hidden for a
  child) and their own scores. Only admins see Admin, children's limits and
  play history.
- The app stores the games played (who, which game and mode, score, level,
  how long, when) and, for limits, when each game started and how long it
  ran. Nothing else is collected.

## Troubleshooting

- **"No admin yet" stays after adding my name** — restart the app; the list is
  only read when the app starts. Check **Settings → How the app sees you** for
  the exact user name to use.
- **A game says it couldn't be loaded** — reload the page; if it stays, the
  browser may be very old.
- **The keys scroll the page instead of playing** — click or tap the game
  first; keys belong to the game only while it's on screen.
- **"Play with someone" shows nobody** — a person appears after they have opened the app once; greyed names
  say why they can't play now.
- **The other phone's score doesn't move** — the bar's dot is grey when that phone has gone quiet. Races still
  work when your network blocks WebSockets; they just check in more often.
- **A child can't start a game** — the start screen says why: no time left
  today, or quiet hours. An admin can add extra time on **Admin → Users** or
  from **Games**.
- **No notifications arrive** — check the person's phone under **Admin →
  Users** and use **Send test**.
