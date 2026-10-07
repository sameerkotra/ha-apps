# Changelog

## 2.4.1

- **Notifications open the app for everyone**: tapping a maintenance notification opened the admin's Settings → Apps page (`/hassio/ingress/…`) — or nothing, for anyone who isn't an admin. It now opens Todo's own sidebar page, learnt from the Supervisor at start-up; an app that isn't in the sidebar sends notifications without a link.

## 2.4.0

- **Answers the Household Assistant**: the new Household Assistant app can ask Todo for a person's tasks ("What's on my list today?" — today's and overdue, this week's, overdue or all; titles, dates, lists and who, never notes), their lists, and what's coming up on the schedule, with a link back to Todo. It can also **add a task** to one of their lists, but only after the person taps the exact change it proposes; the task shows "from Assistant". On by default; an admin can turn it off in **App settings → Household Assistant**, and each person on **Settings → Household Assistant**.

## 2.3.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.3.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 2.3.0

- **Checklists from Household Docs**: *Make a Todo list* in the new Household Docs app turns a checklist into tasks here — in a new list (private or shared) or one you pick. Indented items become the task's checklist, ticked items arrive completed, and each task shows **from Docs**. Todo checks you may add to that list, exactly as in the app; dates, assignees and reminders are then set here as usual, and nobody is notified when the tasks arrive.
- **Admin → App settings → Connected apps**: the other household apps this one exchanges messages with, their version, when they were last heard from and what they can do. Read only.
- The household apps' messages travel over Home Assistant's own event bus — no new permissions. DOCS.md shows how to keep them out of Home Assistant's history.

## 2.2.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Ink is now Midnight; your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged. The maintenance folder is saved with the page's **Save** after **Check**.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- **Done history CSV**: text cells that start with `=`, `+`, `-`, `@` or a tab get a leading `'`, so a spreadsheet can't run them as formulas. Dates and costs are unchanged.
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

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
