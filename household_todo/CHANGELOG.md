# Changelog

## 2.1.0

- Reminders and "assigned to you" notifications for a task or schedule item with a place now show the place's address and phone, with **Directions** and **Call** buttons on phones with the Home Assistant Companion app. The daily digest and weekly summary add the address and phone under the item.
- "Assigned to you" notifications now include the place's name, and tapping them opens the item's link.
- New App setting **Place details in reminders** (on by default) to send only the place's name instead.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Shared household lists and a private list for each person, with due dates, priorities, assignees, checklists and links.
- Calendar and dashboard: month and agenda views, overdue and upcoming tiles, and a workload card per person.
- A schedule of recurring things like trash day, with skips and moved dates, published to Home Assistant as sensors if you want.
- Reminders to each person's phone through Home Assistant: daily digest, weekly summary, "N before" reminders and "assigned to you" pings.
- Places address book with optional drive times from home (off by default; uses OpenStreetMap services when turned on).
- House maintenance with suggestions, Mark done, history, costs, files and reminders.
- Sign-in with your Home Assistant account, in-app admin settings, "act as" another person, and database backup and restore.
