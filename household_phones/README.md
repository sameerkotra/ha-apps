# Household Phones — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

Know whether the household's phones are on and reachable. Each phone checks in with Home Assistant through the
Companion app on its own; Home Assistant asks a phone to report only when it is overdue, and **Check now** says
within seconds whether a phone answers. Everyone opens it from the Home Assistant sidebar with their own Home
Assistant account: each person sees their own phone, admins see every phone. Optionally a phone's messages (a
one-time code…) are forwarded into Household Chat as disappearing messages. See the Documentation tab (DOCS.md) for
the full guide.

- **Only the phones an admin adds** are monitored; each owner can set **do-not-monitor hours**.
- **My phone** — your phone's status, battery and last restart, a setup checklist, and exactly what admins can see.
- **Check now** — "please respond" on demand: online, battery, network and the last forward, in seconds.
- **All phones** (admins) — every phone's status, battery and network, and every sensor Home Assistant has for it.
- **Alerts** — offline, the app not reporting, restarts, low battery and a nightly charging reminder, with cooldowns,
  quiet hours and pauses; one alert when Home Assistant loses contact with all phones at once.
- **Forward my messages** — the owner's choice: a phone's texts or codes into Household Chat, disappearing after an
  hour by default. The text is never stored by this app.
- **Open about monitoring** — pause being watched, do-not-monitor hours, who checked your phone, and a request to
  stop monitoring.
- **Admin** — App settings, people (children), alert tests, backup and restore.
