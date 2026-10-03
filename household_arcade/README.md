# Household Arcade — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

> 🚧 **Under development.** The games and features are still changing from one version to the next: some
> games may need tuning, and saved games or settings may change in a later version.

Classic arcade games for everyone in a Home Assistant home, played from the
Home Assistant sidebar on a computer or in the Companion app on a phone.
Everyone plays as themselves — recognised by their Home Assistant account,
with nothing to sign up for — and the household gets personal bests and a
shared leaderboard. The games run in the browser and are written from scratch:
no ROMs, no emulators, no internet. See the Documentation tab (DOCS.md) for the
full guide.

- **17 games** — Snake, Brick Breaker, Falling Blocks, Paddle Duel, Lane
  Racer, Flap, Mines, Merge, Colour Memory, Memory Cards, Tap the Mole, Number
  Dash, Tank Battle, Sky Defenders, Rocks, Road Hop and Snake Duel (two players
  on one screen, or against the computer). More games are planned. The Games
  page shows them as a list, small squares or large squares.
- **Levels that keep coming** — every game has a list of levels (mazes,
  layouts, courses, arenas, challenges …); optionally an AI model (your own
  Ollama, an OpenAI-compatible service or Anthropic Claude) makes more when
  someone gets near the end, or when an admin asks. Every level is checked by
  the app first, and **Admin → AI usage** shows the tokens (and an estimated
  cost) of every request.
- **Keyboard, touch and game controllers** — arrow keys or WASD, on-screen
  buttons, swipes and taps, dragging the paddle, left- or right-handed controls.
- **Six looks** — Modern, Retro LCD, Neon, Pixel, Paper and High contrast;
  each person picks their own. Sound effects are off until you turn them on.
- **Scores** — personal bests per game and mode, your last 20 games, and a
  household leaderboard for all time and this month. Practice games aren't
  saved.
- **Children** — an admin marks a person as a child and sets daily play time
  (school days and weekends), quiet hours, allowed games and leaderboard
  visibility, and can give extra time today in one tap.
- **Home Assistant extras** — optional sensors (records, minutes played
  today, who is playing) and optional phone notifications for new records and
  "5 minutes left" for parents. Both are off until an admin turns them on.
- **Admin** — in-app settings, levels, users, and database backup and restore.
