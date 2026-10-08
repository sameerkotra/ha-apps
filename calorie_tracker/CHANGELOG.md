# Changelog

## 2.3.0

- **More for the Household Assistant**: "How did I do this week?" gives your last 7 days against your goal, and "I had a banana" logs it — only after you tap the exact entry it proposes, with the calories on the button (the assistant's estimate, or for a food you saved or logged before, its own calories times the servings).

## 2.2.4

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 2.2.3

- **Shared AI code**: the AI connection can offer tools to a model in its own way (used by the Household Assistant); nothing changes in this app.

## 2.2.2

- **Your own Household Assistant switch**: **Goals → Let the Household Assistant answer for me** (on) — turn it off and the Household Assistant gets nothing about your day, whatever the admin's setting.

## 2.2.1

- **Links open the right page**: the Household Assistant's answer opens that day's Food Log. Links ending in `/foodlog/<date>`, `/dashboard`, `/weight` or `/goals` open that page.

## 2.2.0

- **Answers the Household Assistant**: the new Household Assistant app can ask Calorie Tracker "How many calories do I have left today?" and gets only that person's own day — calories and macros against their goals, and what they logged at each meal; never anyone else's, never weight — with a link back to the app. On by default; an admin can turn it off with **Answer the Household Assistant** in App settings.
- Calorie Tracker now joins the household apps' message bus (Home Assistant's event bus). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 2.1.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.1.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.
- Backups still leave out the AI access key; restoring keeps the key this install has (now the same shared code in every app that has access keys).

## 2.1.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Food log by meal for everyone in the household, each under their own Home Assistant login, with a day summary and macro bars against personal goals
- Saved foods for one-click logging
- Weight log with daily, weekly and monthly charts and a target-weight line
- Dashboard with a calorie history chart and a monthly breakdown of calories, macros and weight
- Optional AI calorie and macro estimates and a nutrition chat through your own Ollama, an OpenAI-compatible service or Anthropic Claude; everything else works without AI
- A daily-calories sensor per person in Home Assistant (can be switched off)
- Admin page: App settings, users, and database backup and restore
- Reached only through Home Assistant (ingress): no ports, no extra logins
