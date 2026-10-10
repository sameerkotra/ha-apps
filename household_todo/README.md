# Household Todo — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

Shared and personal to-do lists for everyone in a Home Assistant home, with a
calendar, a schedule of recurring things like trash day, and house
maintenance. Everyone opens it from the Home Assistant sidebar and is
recognised by their Home Assistant account — there is no separate login.
Reminders go to each person's phone through Home Assistant, and schedule
items can show up in Home Assistant as sensors for your automations. See the
Documentation tab (DOCS.md) for the full guide.

- **Lists and tasks** — shared and private lists; tasks with due dates and
  times, priorities, types, places, links, checklists, assignees and
  required/optional completion; drag-and-drop ordering, sorting and filters;
  files on tasks (tickets), removed when the task is done.
- **Calendar and dashboard** — month and agenda views, overdue and upcoming
  tiles, and a workload card per person.
- **Schedule** — recurring items (weekly, every N weeks, monthly, Nth weekday…)
  with skip / move / extra dates, personal and private items, and a
  `binary_sensor` per item.
- **Reminders** — daily digest, weekly summary, "N before" reminders and
  "assigned to you" pings, each switched on per person.
- **Places** — a shared address book, with optional drive times from home
  (off by default; uses OpenStreetMap services when turned on).
- **Maintenance** — house upkeep with suggestions, Mark done, history, costs,
  files and reminders.
- **Checklists from Household Docs** — *Make a Todo list* in Household Docs
  turns a checklist into tasks here (Admin → App settings → Connected apps).
- **Admin** — in-app settings, users and notify services, "act as" another
  person, and database backup and restore.
