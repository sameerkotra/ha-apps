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
remembered on that device. Tap a game's **☆** to make it a favourite: your
**★ Favourites** then come first on the page, in the order you marked them, and
**All games** below (the star in a game's header does the same). Favourites are
your own and follow you to any phone.

The **Search games** box above the games finds them as you type, in every
view: by name (`tic` finds Tic-tac-toe, `sea` Sea Battle), by kind — *arcade*,
*puzzle*, *word*, *board*, *cards*, *dice*, *levels*, *two players* (or
*together*), *turn by turn*, *gentle* (or *kids*, *easy*) — or by a mode
(`7 x 7`). Every word you type has to match. In the large view a game found by
its kind or mode says why ("Mode: 7 × 7"). Press **/** to jump to the box,
**Esc** to clear it, and **Enter** to open the game when only one is left. What
you typed stays while the app is open and is never sent anywhere.

Pick a game to open its start screen,
which sits over a still picture of the game:

- **Mode** (a drop-down) — Snake: *Walls* (hitting the edge ends the game) or *Wrap* (go out
  one side, come back in the other), each at *Slow*, *Normal* or *Fast*, or
  *Maze* (a series of mazes, below).
  Brick Breaker: *Power-ups* or *Classic*. Falling Blocks: *Classic*, *Fast
  start* or *Rising floor*. Paddle Duel: *Easy*, *Normal* or *Hard* (or *Two
  phones*, below). Lane
  Racer: *3 lanes*, *4 lanes* or *Rush*. Flap: *Easy*, *Normal* or *Moving
  gates*. Most games also have a mode that plays a **list of levels** (below:
  *Challenges*, *Stages*, *Courses*, *Waves*, *Gardens*, *Puzzles*, *Towers* …); with AI levels on,
  the list keeps growing. Every mode has its own bests and its own leaderboard.
- **Level** (a drop-down, in modes that carry on) — where a level is a puzzle or a goal (the
  new puzzles' *Levels*, Snake's *Maze*, Mines' *Boards*, Merge's *Goals*, Lights Out's *Climb*,
  Flap's *Course*, the puzzle books and the other level lists) — and since 1.12.1 the score chases and computer
  opponents too (Brick Breaker, Falling Blocks challenges, Paddle Duel, Tank Battle, Sky Defenders, Rocks, City
  Defense waves, Snake Duel vs computer) — the app remembers the levels you've cleared —
  Practice games included — and the next game starts at the first one you haven't:
  *Level 9 (next)*. Pick an earlier level to play it again. When you've cleared them all, the
  next game starts at level 1. The leaderboard and your personal bests count only games
  started from level 1; races and daily challenges always start there. **My scores → Level
  progress** lists your progress with a **Start over** button.
- **Look** (a drop-down) — how the game is drawn (see *Looks*). The picture
  behind changes as soon as you pick one, so you can see it before playing.
  Your choice is remembered.
- **Practice** — play without saving the score.
- **Play** — starts the game. For a child, the start screen also shows the
  time left today. A *Two phones (turn by turn)* mode (the board games and Chess) and Ludo's and Snakes and
  Ladders' *Phones (turn by turn, 2–4)* have no Play button: they are played with someone, each on their own
  phone — **Play with someone** (see *Playing turn by turn*).

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
adds 10 seconds to your time) and **Number lines**: tap a number (on the pad or in the grid) and every
square holding it is highlighted, and the rows, columns and boxes it covers get a soft shade; the empty
squares left clear are the only places that number can still go. Choose how much is shaded:
**Rows, columns and boxes** (the usual), **Rows and columns only** (no box shading), or **None** (no shading
at all — the squares holding the number and the square you picked are still highlighted). Your choice is
remembered for you, and it's only how the board looks: it doesn't change your score, races, daily challenges
or saved games. Your score is
10,000 minus your time in seconds, with 30 seconds added for each hint, so the fastest finish ranks first.
A puzzle you didn't finish scores nothing and isn't kept. A game can be saved and carried on later.

### Word Guess

Find the hidden word. The first guess is any 5-letter word you like — that's where the clues come from. Type a guess (or tap the on-screen keys) and press Enter: blue with a dot is the right
letter in the right place, orange with a diamond is in the word but elsewhere, dim is not in the word (a key
under the grid repeats this). **Six tries**
(5 letters), **Eight tries**, or **Strict**, where every clue you've been given must be used in the next guess.
The start screen's **Clue at the start** can show one letter in its place before your first guess; it costs 500
points, and races and daily challenges always start without it so everyone plays the same game.
Only real words are accepted. Score: 1,000 for each try left (plus one) and up to 999 for speed,
so fewer guesses rank first and then the faster time. A word you didn't find scores nothing.

### Word Search

Find the hidden words in the grid by dragging across them (they run in straight lines, any
direction except on **Little ones**, where they only run right and down). **Little ones**
(7 × 7, 5 words), **Kids** (9 × 9), **Everyone** (11 × 11) and **Puzzler** (13 × 13, 12 words).
**Hint** shows one word to look for and costs 25 points. Each word is worth 100, with a bonus for
finishing quickly. **Themes** plays a list of named themes (At the farm, Under the sea, Space trip …), one a game, each
for one age group and its grid; the next game starts at the next theme, and with AI levels on the list grows.

### Bubble Pop

Coloured bubbles hang from the ceiling. Aim the launcher at the bottom and shoot: the bubble flies straight,
bounces off the side walls and sticks where it touches. Three or more of a colour touching each other pop (10
each), and any bubbles left hanging from nothing drop (20 each). The bubble you shoot and the next one (shown
beside the launcher — swap them with ↓, C or a tap on it) are always colours still on the board. Every few shots
the ceiling comes down a row (the dots beside the launcher count down); a bubble below the dashed line ends the
game. Clearing the board scores 500 and brings the next one. Each colour also has its own small mark, so the
colours can be told apart without colour too.

**Classic** gets harder board by board; **Relaxed** is the gentle one for little ones (at most four colours,
small boards, the ceiling only every 14 shots); **Puzzles** plays the list of puzzles (the last one wins);
**Endless** pushes a new row in from the top instead of lowering the ceiling, and never ends until the bubbles
come too low.

### Gem Swap

Swap two neighbouring gems to line up three or more of the same kind (each kind has its own shape as well as
its colour). The line clears, the gems above fall and new ones drop in — and any new lines clear too, each one
in a row worth more (up to five times). A swap that makes no line swaps back. Four in a line leaves a **line
gem** (it clears its whole row or column when it goes), an L or T shape a **blast gem** (the 3 × 3 around it),
and five in a line a **star** (swap it with any gem to clear every gem of that kind). Locked gems can't be moved
until a line through them breaks the lock. When no move is left the board is shuffled. **Hint** shows a move.

**Timed (90 s)** — as many points as you can in a minute and a half. **Moves (levels)** — each level asks for a
number of points (and to break its locks) within a number of moves; moves left over score 50 each; the last
level wins. **Zen** — no clock and no end: play as long as you like and end the game from the pause screen.

### Tower Stack

A block slides from side to side above your tower; drop it on the one below. Whatever hangs over the edge is
cut off and falls, so the next block is only as wide as what was left. Line it up within a hair for a **perfect
drop**: it keeps the full width, scores extra, and a run of three or more grows the block back a little. Each
floor is a little faster. Missing the tower completely ends the game.

**Classic**, **Fast**, **Easy (3 tries)** — wide and slow, and a miss only costs one of three tries: good for
little ones — and **Towers**, a list of buildings with their own height, width and speed (3 tries for the whole
list; finishing the last one wins).

### Runner

Your runner races to the right on their own. Jump over boxes and pits (hold the jump for a higher one — the
tall boxes need it), duck under hanging bars, and jump or duck the fliers. Coins along the way are 10 each, and
every 10 steps of ground is a point. It gets faster as you go. **Endless** has one life; **Easy** is slower with
three lives; **Courses** plays a list of set courses (3 lives for the whole list; 100 for each finished, the
last one wins). After a bump you get a moment to get going again.

### Lander

Bring the lander down gently on a landing pad. Gravity pulls it down; the engine pushes the way the lander
points and uses fuel. Turn it with the left and right buttons and fire the engine to slow down. Land upright
and slowly — the speeds at the top turn green when a landing would be safe — on one of the pads: the small
ones (×3 to ×5) score more than the wide ones (×1, ×2). A landing scores the pad × 100 plus half the fuel left,
and the next level comes. Anything else is a crash and costs one of 3 landers (that level again). Out of fuel,
the lander just falls. Some levels have wind.

**Classic** gets harder level by level; **Easy** has lighter gravity, wide pads, no wind and plenty of fuel;
**Levels** plays the list of levels (the last one wins).

### City Defense

Missiles fall toward your six cities and three bases. Tap the sky where an interceptor should burst: the
nearest base with ammo fires it, and its cloud stops every missile inside it (25 each). Aim a little ahead of
the missiles. Some split into three on the way down, and fliers cross the sky dropping missiles of their own
(100). Each base has a few shots a wave, and a base that is hit is out until the next wave. When a wave is over,
each shot left is worth 5 and each city still standing 100; every third wave a lost city is rebuilt. The game
ends when no city is left.

**Classic**, **Easy** (slower and fewer missiles, more shots) and **Waves** (a list of waves; the last one
wins).

### Slide Puzzle

Put the tiles back in order — 1, 2, 3 … with the gap at the bottom right. Tap a tile in the gap's row or column
to slide it (and the tiles between) into the gap, or swipe; the arrow keys slide the tile next to the gap that way.
**3 × 3**, **4 × 4** and **5 × 5** have numbers; **Picture 3 × 3** (the gentle one for little ones) and **Picture
4 × 4** show a picture cut into pieces — a house, a boat, a rocket, a flower, a fish or a balloon — with small numbers
in the corners to help (the start screen's **Numbers on picture tiles** turns them off). Hold **👁 Peek** (or C) to see
how it should look. Every shuffle can be solved. Each tile moved is a move: your score is 10,000 minus your moves,
so the fewest moves rank first. A tray you didn't finish scores nothing.

### Lights Out

Switch every light off. Pressing a light switches it and its four neighbours — lit ones off, dark ones on. Tap a
light, or move the ring with the arrows and press Space. **Little 3 × 3** is the gentle one, then **5 × 5** and
**7 × 7**; **Climb** plays five boards from 3 × 3 up to 7 × 7. Every board can be solved, and when it's done the game
tells you the fewest presses it could have taken. Your score is 10,000 minus your presses. With **Hints** turned on
(start screen), **💡 Hint** (or C) rings a light to press; each one costs 5 points. Races are always played without
hints.

### Picture Logic

Fill squares to find a hidden picture. The numbers beside each row and above each column are its runs of filled
squares, in order: "3 1" means three filled squares, a gap of at least one, then one more. Tap a square to fill it,
tap again to empty it, and drag to fill a line. **■ / ✕** switches your taps to crosses — your own notes for squares
that stay empty. The numbers of a line dim once it's right. Every puzzle has exactly one answer and can be worked out
from the numbers alone, without guessing (now and then a square or two are given at the start, marked with a dot).
**5 × 5** is the gentle one; **8 × 8**, **10 × 10** and **15 × 15** are bigger. The start screen's **Mistakes** choice
works as in Sudoku: shown at once (the square is put right, +10 seconds) or only when the grid has as many filled
squares as the answer. **Hint** puts one square right (+30 seconds, 3 a puzzle); **Undo** takes back your last stroke.
Your score is 10,000 minus your time in seconds with the extra seconds added, so the fastest finish ranks first.
The **Picture book** has drawn pictures — a heart, a house, a cat … — one a game, with the picture's name shown once
it's solved; the next game starts at the next picture, and with AI levels on the book grows.

### Tile Match

Clear the heap two tiles at a time: tap two **free** tiles with the same symbol (the same number and the same small
shape under it) and they go. A tile is free when nothing lies on it and its left or right side is open; tiles that
aren't free are a little dimmer. Every deal can be cleared. **Hint** shows a pair (+15 seconds), **Shuffle** deals
the tiles left again so they can still be cleared (+30 seconds) and **Undo** puts the last pair back. **Little** (20
tiles with plain shapes, for little ones), **Classic** (72 tiles) and **Big heap** (104). **Layouts** plays heaps of
other shapes (a turtle, a castle, a butterfly …), one a game, carrying on from the next one. Your score is 10,000 minus
your time in seconds with the extra seconds added.

### Code Breaker

Find the hidden code. Fill a row with symbols — tap them, or type their numbers — and press **Check** (Enter). A
**solid dot** means one symbol is right and in the right place; a **hollow ring** means one is in the code but
somewhere else. Tap a placed symbol (or press Backspace) to take it out. Every symbol has its own number and shape as
well as its colour, so the colours never have to be told apart. **Little** (3 symbols from 4, no repeats, 8 rows) is
the gentle one; **Classic** (4 from 6, repeats allowed, 10 rows), **No repeats** (4 from 6) and **Master** (5 from 8,
12 rows). Score: 1,000 for each row left (plus one) and up to 999 for speed — fewer rows rank first, then the faster
time. A code you didn't crack scores nothing (the code is shown).

### Type Rain

Words fall from the sky: type each one before it lands. The first letter you type picks the lowest word that starts
with it; then type the rest of its letters (the key for the next one lights up). A wrong letter is a slip; Backspace
lets go of a word. A word scores 10 a letter, doubled after 10 words in a row without a slip and tripled after 25.
A word that lands costs one of your raindrops (lives). Type on a keyboard, or tap the keys drawn on the game on a
phone. **Little ones** drops single letters slowly, with 5 raindrops; **Easy** has short words; **Classic** gets
faster and the words longer every 20 seconds; **Stages** plays a list of stages (200 for each one cleared; the last
one wins).

### Board games: Four in a Row, Tic-tac-toe, Checkers, Reversi, Dots and Boxes, Sea Battle

Six games for two, each played three ways (the **Mode** on the start screen):

- **Computer · Easy (gentle)**, **Medium** or **Hard** — you against the app, and you always move first. Hard is
  strong (in Tic-tac-toe it never loses, so a draw against it is a good result).
- **Two players (one screen)** — take turns on one phone or computer. Nothing is saved.
- **Two phones (turn by turn)** — with someone else in the household, each on your own phone; you don't need to be
  online at the same time (next section *Playing turn by turn*).

Tap where you want to play, or move the cursor with the arrows and press **Space** (a controller: the d-pad and A).
The two players are named at the top (with their discs, pieces or boxes so far); a line under one shows whose move
it is, and the line at the bottom says what's happening. Pieces always differ by shape as well as colour.

- **Four in a Row** — drop discs into the frame; four in a row across, up and down or slanting wins. Tap a column
  (or ← → and Space or ↓).
- **Tic-tac-toe** — three in a row on a 3 × 3 grid. ✕ goes first.
- **Checkers** — English draughts: men move one square diagonally forward, reaching the far row crowns a king (★,
  moves both ways); jumping is compulsory, and a jump that can carry on must carry on (multi-jumps); you may choose
  which capture when there are several. Take all the other's pieces, or leave them no move, to win; 40 moves each
  without a capture or a man moving is a draw. Tap a piece (dots show where it can go), then the square; a
  multi-jump finishes by itself when there's only one way on, otherwise tap each landing square. C lets go of a
  piece. On the second phone of a match the board is turned round so your pieces are at the bottom.
- **Reversi** — place a disc to trap a line of the other's discs, which turn over. Dots show where you may play; a
  player with nowhere to play passes automatically. When neither can play, more discs wins.
- **Dots and Boxes** — draw a line between two dots next to each other; drawing a box's fourth side claims it and
  you draw again. More boxes wins. **Size** on the start screen: 3 × 3, 4 × 4 or 5 × 5 boxes.
- **Sea Battle** — place your fleet (it starts arranged at random: tap a ship to pick it, again to turn it, tap a
  square to move it; **Shuffle**, then **Ready**), then take turns firing at the other's hidden sea: a cross is a
  hit, a dot a miss, a sunk ship is outlined. Sink the whole fleet to win. Your own sea is the small one at the
  bottom. **Fleet** on the start screen: *Classic* 10 × 10 with five ships or *Small* 8 × 8 with four. On one screen,
  a "Pass to …" screen hides the seas between turns — hand the phone over and tap when ready.

Score (against the computer and from two phones): a win scores 100 (Easy), 250 (Medium) or 500 (Hard, and against
a person), plus a bonus for how well you won (empty places left, pieces or discs or boxes more than the other, your
fleet unhit), at most as much again; a draw scores a quarter; a loss isn't saved. Each mode has its own leaderboard.
Games against the computer and on one screen can be saved for later.

### Ludo and Snakes and Ladders

Dice games for 2 to 4 players, each played three ways (the **Mode**):

- **You and the computer** — you against one to three computer players (**Players** on the start screen).
- **One phone (pass and play)** — everyone on one phone or computer, taking turns; with **One phone: empty seats**
  set to *The computer fills them*, the computer plays the seats up to four. Nothing is saved.
- **Phones (turn by turn, 2–4)** — with up to three others in the household, each on your own phone, whenever suits
  you (see *Playing turn by turn*). The app rolls the die for whoever's turn it is: nobody can choose or re-roll.

Tap the die (or press **Space**) to roll. When the roll leaves no choice (no token can move, or only one way to
move), the move is made for you.

- **Ludo** — get all four of your tokens home first. A **6** brings a token out of your yard onto your start square
  and gives you another roll (three 6s in a row lose the turn). Tokens go round the board clockwise, then up your own
  coloured column; home needs the exact roll. Landing on other players' tokens sends them back to their yards —
  except on the starred squares and the start squares, which are safe. Your own tokens may share a square. Tap one
  of your ringed tokens to move it (or the arrows and Space). Tokens differ by shape as well as colour: red ●,
  green ■, yellow ▲, blue ◆.
- **Snakes and Ladders** — race to square 100: a ladder's foot takes you up, a snake's head slides you down. Your
  token moves by itself. **Board**: *Classic* or *Gentle* (short snakes, for small children). **Finish**: *Reach 100*
  (passing it wins too) or *Exactly on 100* (a roll that would go past is lost).

Score: winning against the computer scores 100 for each computer player in Ludo (100 in Snakes and Ladders), from
phones 500, plus a bonus (Ludo: the others' tokens not home; Snakes and Ladders: how far the nearest other player
still had to go), at most as much again; a loss isn't saved. Games against the computer and on one phone can be
saved for later.

### Carrom

Flick the striker to knock your coins into the four corner pockets. The **Mode**: **Computer · Easy (gentle)**,
**Medium** or **Hard**; **Two players (one screen)**; or **Doubles** (four players in two teams on one screen,
partners opposite).

- **Shooting**: drag the striker along your baseline to place it, then pull back from it like a catapult — the line
  shows where it will go and how hard — and let go. Keys: ← → place it, ↑ ↓ aim, hold **Space** for power (it rises
  and falls) and let go to shoot; **C** sets the aim straight again. A controller: the d-pad and A.
- **The rules** (a common family set): the first player plays White and breaks. Pocketing one of your coins (or the
  red queen) gives you another shot. The queen must be **covered**: pocket one of yours in the same or your next
  shot, or the queen goes back to the middle. Pocketing the striker is a **foul**: the shot's coins come back, and so
  does one of yours already pocketed. Your last coin can't go down while the queen is still on the board. The first
  to pocket all nine of their coins wins the board, with a point for each of the other's coins left and 3 for the
  queen. A board still going after 300 shots ends there, on the coins pocketed.

Score: a win scores 100 (Easy), 250 (Medium) or 500 (Hard, and against a person), plus 20 a point, at most as much
again; a board on one screen, a loss, or a board ended early isn't saved.

### Chess

Chess with all the rules: castling, en passant, promotion (you choose Queen, Rook, Bishop or Knight), check,
checkmate and stalemate; draws by the same position three times, 50 moves each without a capture or a pawn move,
and too few pieces to mate come by themselves. The **Mode**: **Computer · Easy (gentle)**, **Medium** or **Hard**
(it thinks for up to a second or two), **Two players (one screen)**, or **Two phones (turn by turn)** (see *Playing
turn by turn*). Against the computer, **Against the computer, play** picks White, Black or either.

Tap a piece (dots show where it can go), then its square; or the arrows and Space, **C** to let go. The last move is
marked and a king in check is ringed. Playing Black, the board is turned round so your pieces are at the bottom.
Score as for the board games above (a win 100 / 250 / 500 plus 10 a point of material left; a draw a quarter).

### The calm puzzles: Arrow Release, Car Park, Colour Sort, Bolt Sort, Dot Connect, Untangle

Six puzzles without a rush. Each has a gentle mode, bigger ones, and **Levels**: 200 numbered boards, the same for
everyone, that get harder as you go; a cleared board leads to the next, the score adds up, and the next game starts
at your next level (see *Level* above). End a Levels run from the pause screen to keep its score. Every board is
checked to be solvable before it's shown. A board scores 10,000 less its costs (at least 10), only when it's solved;
an unfinished one scores nothing. The start screen's **Hints** option (off by default, always off in races) adds a
💡 **Hint** button (or **C**) that costs 200 points a hint. With the keyboard, the arrows move a ring and **Space**
acts. Every piece carries a number or shape as well as its colour.

Besides *Levels*, each has a list of named boards: Arrow Release's **Picture boards** (arrows filling a picture —
a heart, a rocket, a tree) and the **Puzzle book** of the other five. They play like *Levels*, and with AI levels on
the list grows (see *AI levels*).

- **Arrow Release** — tap an arrow and it flies off the way it points, if nothing is in its way; if something is, it
  bumps and you lose a heart (three; *Little 5 × 5* has none, and *Levels* gives one back for each cleared board).
  Every arrow has a tail, and the tail blocks the others too. Releasing arrows only ever makes room, so look for the
  ones with a clear way out. The boards are big and the arrows small: **pinch** to zoom in (or turn the mouse wheel,
  or press **－** / **＋**), and drag to look around. Modes: *Little 5 × 5*, *14 × 14*, *20 × 20*, *Twisty 16 × 16*
  (bent arrows), *Levels* (6 × 6 growing to 24 × 24) and *Picture boards*. Costs: 10 a second, 300 a bump.
- **Car Park** — get the red car out through the gap on the right. Cars and lorries only move along their length:
  drag one, or ring it, press Space and slide it with the arrows. One slide, however far, is one move; the fewest
  moves is shown, and **Undo** takes a slide back. Modes: *Little*, *Classic*, *Hard*, *Levels*. Costs: 5 a second,
  50 a move over the fewest.
- **Colour Sort** — tap a tube, then another: the top colour pours across (every layer of it that fits) onto an
  empty tube or the same colour. Sort every colour into a full tube of its own. **Undo** and **Restart**. Modes:
  *3 colours*, *7 colours*, *10 colours*, *Levels*. Costs: 5 a second, 10 a pour.
- **Bolt Sort** — the same idea one nut at a time: a nut moves onto an empty bolt or a nut of its colour with room.
  In *6 colours, hidden* (and later levels) the nuts below the top show "?" until they reach the top. Modes:
  *3 colours*, *6 colours*, *6 colours, hidden*, *Levels*. Costs: 5 a second, 10 a move.
- **Dot Connect** — join each pair of dots with the same number by a line through neighbouring squares. Lines can't
  cross (drawing over one cuts it) and every square must be filled (*Little 5 × 5* only asks for the pairs). Drag
  from a dot or a line's end; with the keyboard, Space on a dot starts a line, the arrows draw and Space lets go.
  Modes: *Little 5 × 5*, *7 × 7*, *9 × 9*, *Levels*. Costs: 10 a second.
- **Untangle** — drag the points until no two lines cross; lines that cross are dashed, and the count is under the
  board. With the keyboard, Space picks the next point and the arrows move it. Modes: *6 points*, *10 points*,
  *16 points*, *Levels*. Costs: 10 a second, 20 a move.

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
| Tank Battle | Arrow keys drive, Space fires | The arrow buttons and **Fire**, or hold a finger on the game to drive toward it and tap your tank to fire |
| Sky Defenders | ← → move, Space fires (hold to keep firing) | Drag on the game to move, tap to fire, or ◀ ▶ and **Fire** |
| Rocks | ← → turn, ↑ thrust, Space fires | ⟲ ⟳ ▲ and **Fire**; a tap on the game fires |
| Road Hop | Arrow keys (or W A S D) hop, Space hops forward | Swipe, tap to hop forward, or the arrow pad |
| Snake Duel | Green: arrow keys; blue: W A S D | Swipe on the right half for green, the left half for blue |
| Bubble Pop | ← → aim, Space or ↑ shoots, ↓ or C swaps in the next bubble | Drag on the game (or the strip below it) to aim, let go to shoot, or **Shoot**; tap the next bubble to swap |
| Gem Swap | Arrows move the ring, Space picks a gem up, then an arrow swaps it; C shows a move | Tap a gem and then its neighbour, or drag a gem toward the one to swap with; **Hint** |
| Tower Stack | Space, ↑ or ↓ drops the block | Tap the game, or **Drop** |
| Runner | Space or ↑ jumps (hold for higher), ↓ ducks | Tap the game to jump (hold for higher), drag down to duck; or **▲ Jump** and **▼ Duck** |
| Lander | ← → turn, ↑ or Space fires the engine | ⟲ ⟳ and **▲ Engine**; or hold a finger on the game for the engine |
| City Defense | Arrows move the crosshair, Space fires | Tap the sky where the interceptor should burst |
| Slide Puzzle | Arrow keys slide the tile next to the gap; hold C to peek | Tap a tile or swipe; hold **👁 Peek** |
| Lights Out | Arrows move the ring, Space presses; C hint (when hints are on) | Tap a light; **💡 Hint** |
| Picture Logic | Arrows move the ring, Space fills, X crosses, M switches fill / cross, H hint, U undo | Tap or drag; **■ / ✕**, **💡 Hint**, **↶ Undo** |
| Tile Match | Arrows move the ring, Space picks; H (or C) hint, S shuffle, U undo | Tap two tiles; **💡 Hint**, **🔀 Shuffle**, **↶ Undo** |
| Code Breaker | 1–8 place a symbol, Enter checks, Backspace takes one out | Tap the symbols, **Check** and **Delete**; tap a placed symbol to take it out |
| Type Rain | Type the letters; Backspace lets go of a word | Tap the keys on the game |
| Four in a Row | ← → pick the column, Space or ↓ drops | Tap a column |
| Tic-tac-toe, Reversi | Arrows move the cursor, Space plays | Tap a square |
| Checkers | Arrows move the cursor, Space picks a piece and then where it goes; C lets go | Tap a piece, then where it goes |
| Dots and Boxes | ← → move the dashed line along, ↑ ↓ to the lines that cross; Space draws it | Tap between two dots |
| Sea Battle | Arrows move over the sea (and, while placing, down to Shuffle / Turn / Ready); Space acts; C turns the picked ship | Tap a ship, a square or a button; tap their sea to fire |
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
| Bubble Pop | ← → aim | Shoot | Swap in the next bubble |
| Gem Swap | Move the ring (then: swap that way) | Pick up a gem | Show a move |
| Tower Stack | — | Drop | — |
| Runner | ↑ jump, ↓ duck | Jump (hold for higher) | — |
| Lander | ← → turn, ↑ engine | Engine | — |
| City Defense | Move the crosshair | Fire | — |
| Slide Puzzle | Slide the tile next to the gap | — | Peek (hold) |
| Lights Out, Picture Logic, Tile Match | Move the ring | Press / fill / pick | Hint (Lights Out, Tile Match), cross (Picture Logic) |
| Code Breaker | ← → pick a symbol, ↑ check, ↓ delete | Place the symbol | Delete |
| Type Rain | Move a ring over the keys drawn on the game | Type the ringed key | Let go of a word |

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
4. When both have finished you see both results side by side and who won (or a draw). For the puzzles (Sudoku, Word Guess and the wave 6 puzzles)
   the card also says how your game went ("Found SMALL in 3 tries of 6"), and a puzzle you didn't solve shows
   **Not solved — scores 0**. Each score is also saved
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
  Memory Cards, Tap the Mole, Number Dash, Sky Defenders, Rocks, Road Hop, Sudoku, Word Guess, Word Search,
  Bubble Pop, Gem Swap, Tower Stack, Runner, Lander and City Defense (the same boards, gems, blocks, course,
  ground or missiles on both phones; in Lander fuel left counts in the score, so of two landings the one with more
  fuel left wins), and Slide Puzzle, Lights Out, Picture Logic, Tile Match, Code Breaker and Type Rain (the same tray,
  board, picture, heap, code or falling words). In Slide Puzzle and Lights Out the first to solve wins (finished in
  the same second: fewer moves); Picture Logic and Tile Match go by the score (the time with hints and shuffles
  added); Code Breaker by fewer rows, then time; Type Rain by the score, and the same score goes to the quicker
  typist. Two-player games (Snake Duel, Paddle Duel, Tank Battle) aren't raced.
- An admin can turn the phone notification off with **Invites by phone notification** in **App settings**;
  everyone can also turn off **Receive notifications** on Settings. The pop-up and the Games page card stay.

### Playing turn by turn (two phones)

The board games' and Chess's **Two phones (turn by turn)** mode, and Ludo's and Snakes and Ladders' **Phones (turn
by turn, 2–4)**: you and others in the household play one game, each on your own phone, taking turns — whenever
suits you. A game can last minutes or days.

- **Ludo and Snakes and Ladders**: tick up to three people in **Play with someone**. The game starts when everyone
  has joined — or the one who invited can tap **Start with 2** (or 3) to begin with those who have. The app rolls the
  die. If someone resigns, the computer plays their tokens to the end (🤖 next to their name); the best-placed
  person still playing wins.

1. On the game's start screen pick **Two phones (turn by turn)** (and the Size or Fleet), tap **Play with
   someone** and pick a person. They get the invite as for a race (notification, pop-up, **Waiting for you**); a
   turn-by-turn invite lasts **7 days**. You don't have to wait on the page: **Back to games**.
2. When they join, the game starts; who moves first is chosen at random. The one whose move it is gets a phone
   notification "Your move in Four in a Row against Asha" (tap it to open the game). The **Games** page has
   **Your games**: every match going on, whose move it is and when the last move was — tap **Play** or **Open**.
3. Make your move; it goes to the app, which checks it (an illegal move, or one from the wrong phone, is refused)
   and tells the other player. If they have the game open too, their board updates within a moment.
4. Pause offers **Back to games (the match waits)** and **Resign** (the other player wins). If a player doesn't move
   for 7 days, the match ends and they lose.
5. At the end both see who won; each player's score is saved as a game of the *Two phones* mode (not Practice; a
   loss isn't saved), the result counts in **Against others**, and **Rematch** invites again.

Good to know:

- You can have several turn-by-turn games going at once, and they don't stop you playing anything else or racing.
- Sea Battle's fleets stay on the app: your phone never gets the other player's ships — only where your shots hit
  or missed and which ships sank — until the game is over.
- Children: every move checks their limits — a game that isn't one of theirs, quiet hours or no play time left
  means they can see the board but not move until they may. The time spent on the game's page counts as play time,
  whoever's move it is. They aren't sent your-move notifications during their quiet hours (it comes afterwards).
- Your-move notifications come at most once every 15 minutes per game. An admin can turn them off with
  **Your-move notifications** in App settings; everyone can turn off **Receive notifications** on Settings.

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
- **Let the Household Assistant answer for me** (on) — shown while an admin
  lets the Household Assistant ask Arcade.
- **Your limits** — for a child: today's time left, daily minutes, quiet hours
  and games, read-only.
- **How the app sees you** — the user name and id Home Assistant sent, and
  whether you are an admin in this app.
- **Theme** (Midnight, Slate, Daylight, or Auto, which follows your device's
  light or dark setting) — in the sidebar on a computer, on Settings on a phone.
  If you had picked Ink before, you now get Midnight. The game looks below are
  separate and unchanged.

## The Household Assistant

If the household also uses the **Household Assistant** app, you can ask it about
Arcade — "Who has the high score in Snake?", "What's my best in Falling Blocks?". Arcade
tells it:

- the **leaderboard**: one game's top 10 (all time or this month), or who holds
  the record in each game — only while the leaderboard is on, and never to a
  child;
- **your own scores**: your best in each game and how long you played this
  week (children can ask for their own).

Each answer links back to the Leaderboard or My scores. An admin can turn this
off for everyone (**App settings → Household Assistant**), and you can turn it
off for yourself on **Settings → Let the Household Assistant answer for me**.

The answers travel through Home Assistant's event bus, which Home Assistant's
recorder keeps in its history unless told not to. Add this to Home Assistant's
`configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

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

The settings are in cards (Games, Looks and scores, Children, Home Assistant,
Household Assistant, AI levels), with a line under each setting saying what it does, its range and
its default. Changes are kept until you select **Save** at the bottom (it shows
how many unsaved changes there are); **Discard changes** puts everything back.
A number out of range is flagged at the field before anything is saved.

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
| Answer the Household Assistant | on | Lets the Household Assistant app tell people the leaderboard and their own bests (see *The Household Assistant*). |
| Home Assistant sensors | off | See below. |
| AI levels | off | More levels made by an AI model. See *AI levels* below. |

### AI levels

Most games have a list of levels — Brick Breaker's layouts, Snake's mazes,
Flap's courses, Tank Battle's arenas, Number Dash's challenges, Type Rain's
stages, Picture Logic's picture book, Word Search's themes, Tile Match's layouts,
the puzzle books of the calm puzzles and so on — that starts with built-in levels
(Sudoku, Word Guess, Slide Puzzle, Lights Out and Code Breaker make a fresh puzzle
each game instead, as do the other modes of the puzzles). Every level a model
writes is checked first, and the puzzles' are solved by the app before anyone sees
them. With **AI levels** on, an AI model makes more, so
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
`notify.…` service here for anything else) and **Send a test**. **Level
progress** shows the levels the person has cleared, with **Start over** for a
game and mode (their scores are kept).

### Storage

**Download database** saves a complete backup (a `.db` file). **Import
database** replaces everything in the app with a backup; a backup from an
older version is brought up to date automatically. Home Assistant's own
backups include the app's data too. Backups leave out access keys and passwords; after restoring on a new install, enter them again. (Importing keeps the AI
access key this install already has.)

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

**Your-move notifications** (on by default) sends "Your move in Checkers against Asha" to the player whose move it
is in a turn-by-turn game, with an **Open** button, at most once every 15 minutes per game and never in a child's
quiet hours (it is sent when they end). It follows the person's **Receive notifications** choice too.

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
  Users** and use **Send a test**.
