# Calorie Tracker

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

A food log, macro tracker, weight tracker and goal setter for everyone in your household, right
inside Home Assistant. Each person's Home Assistant login is their account, so there is nothing
to sign up for and everyone sees only their own data. An optional AI helper estimates calories
and macros from a plain description ("two slices of wholemeal toast with butter") and answers
nutrition questions, using your own Ollama server, an OpenAI-compatible service or Anthropic
Claude. Each person's calories for today can appear as a Home Assistant sensor for dashboards
and automations.

- Food log by meal, with day summaries, macro bars and goals
- Saved foods for one-click logging
- Weight log with daily, weekly and monthly charts
- Calorie history and a monthly breakdown on the dashboard
- Optional AI estimates and nutrition chat (bring your own AI; works without it)
- A `sensor.calorie_tracker_<user>_daily_calories` per person (can be switched off)
- Admin page: app settings, users, database backup and restore
- Reached only through Home Assistant — no ports, no extra logins

See the **Documentation** tab for setup and the full guide.
