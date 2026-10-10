# Changelog

## 2.7.0

- **Files on tasks (tickets)**: attach a concert ticket, a boarding pass, a booking confirmation or a photo to any task — **Edit task → Files → + Attach** (or drop them; a phone can take a photo). Up to 10 files per task, 25 MB each, kept in the files folder (`Tasks/<task>/`); the task row shows 📎 and how many. Only people who can see the task can open them, so a task on your personal list keeps its files private.
- **Gone when it's done**: ticking a task off removes its files — unless **Keep after done** is on for a file — and unticking it within 30 days brings them back. Deleting a task (or its list) removes all its files; the folder keeps them in `_deleted` for 30 days. With the folder not connected, the files move once it's back.
- **Open ticket**: a task's reminder (and "Remind me at") for a task with exactly one PDF has an **Open ticket** button, which opens the file in the app with **Open** and **Download**.
- The Household Assistant mentions how many files a task has ("Flight (today, 2 files attached)"); it never reads or sends them.
- App settings: *Maintenance files folder* is now **Files folder** (Maintenance doesn't need to be on to attach files to tasks). Files already on Maintenance jobs, and new ones, start with Keep after done on, so jobs keep their receipts and photos as before.

## 2.6.0

- **Take turns**: a household schedule item can go round people in order (*Take turns* in its form) — bins week by week to Asha, Kabir, Meera … Each date shows whose turn it is (and can be handed to someone else for that one date); the calendar, "Mine", the dashboard, the daily digest and "before it starts" reminders follow whose turn it is, and the Home Assistant sensor has a `turn` attribute. Skipping a date doesn't change whose the next one is.
- **"Remind me at 5 pm"**: the Household Assistant can set a reminder for a time — it adds the task to your list and sends one notification at that time.

## 2.5.0

- **More for the Household Assistant**: tick off a task ("I took the bins out") or move one to another day ("move the plumber to Friday") — only after you tap the exact change it proposes; when several open tasks match, nothing changes and it asks which. It can also say who has what: "What's Meera doing this week?", "What's left that nobody has?".

## 2.4.4

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 2.4.3

- **The Household Assistant sees the schedule too**: asked for tasks for today, tomorrow or the week, Todo also
  says what's on the Schedule those days (as the Calendar shows both), so "tasks for today and tomorrow" no longer
  misses a dentist appointment or trash day. A new *tomorrow* choice, and when the days asked about have no tasks,
  the next ones due after them, so "nothing today" isn't read as "nothing this week".

## 2.4.2

- **Links open the right page**: a maintenance notification opens the Maintenance tab, and the Household Assistant's answers open the Dashboard, the list they talk about, Lists or Schedule. `#/lists/<id>` opens a list.

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
