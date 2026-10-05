# Household Todo

Household Todo is a shared to-do app for everyone in a Home Assistant home. It
has shared lists for the household and private lists for each person, a
calendar, a schedule of recurring things (trash day, recycling every other
week, a weekly class), a small address book of places, house maintenance, and
reminders sent to phones through Home Assistant. There is no separate login:
everyone opens it from the Home Assistant sidebar and is recognised by their
Home Assistant account.

## Getting started

1. In Home Assistant, go to **Settings → Apps → Install app**, open the
   **⋮** menu (top right) → **Repositories**, paste
   `https://github.com/sameerkotra/ha-apps` and select **Add**.
2. Still in **Settings → Apps → Install app**, find **Household Todo** and
   **Install** it (the first install builds the app and takes a few minutes),
   then **Start** it on its **Information** tab. Turn on **Show in sidebar**
   there too.
3. Open it from the sidebar. Until an admin is set, every page shows a
   **"No admin yet"** banner with your Home Assistant user name in it.
4. In the app's **Configuration** tab, add that user name to
   **`admin_users`**, **Save**, and restart the app (**Information** tab →
   **Restart**). (Your user name is
   also shown under **Settings → How the app sees you** in the app.)
5. Back in the app you now have **🛡️ Admin** in the sidebar. First steps:
   - **Admin → App settings**: decide whether to publish schedule items as
     Home Assistant sensors, and whether to turn on **Drive times** (see
     *Where your data goes*).
   - **Admin → Users**: check everyone's phone for reminders (see
     *Reminders*). People appear here after they have opened the app once.
   - **Admin → Maintenance**: turn on house maintenance if you want it.
   - Add a first task with the quick-add box on the **Dashboard** or in
     **Lists**.

Only `admin_users` is set in the Configuration tab; everything else is in the
app and changes apply straight away, without a restart.

## Lists and tasks

- **Lists.** A new install has one shared list, **Household**, and each
  person gets a private personal list, **My Tasks**, the first time they open
  the app. Anyone can add more lists: *Shared* (everyone sees it) or *Only
  me* (private). A list can be renamed or deleted (deleting removes its
  tasks); whether it is shared or private can't be changed later. Drag the
  list cards to reorder them.
- **Tasks.** Only a **title** is required. Optional: notes, a **link**, a due
  **date** and **time**, a **priority** (low, medium, high), a **type**
  (Appointment, Doctor appointment, Errand, Chore, Bill, or your own), a saved
  **place**, an **assignee**, and a **checklist**.
- **Required or optional.** Each task is *Optional* (the default) or
  *Required*. A required task shows as **overdue** once its date has passed
  and counts in the overdue numbers; an optional one is only shown as *past*
  and never nags.
- **Checklists.** Tick items off inside the task row (a `☑ 2/5` chip with a
  progress bar expands it) or edit them in the task. The checklist and
  completing the task are independent.
- **Assigning.** A task in a shared list can be assigned to anyone who isn't
  disabled; a task in a personal list only to its owner. The person gets an
  "assigned you" notification if they have that switched on.
- **Moving.** In **Edit task**, the **List** picker moves a task to another
  shared list or to your own personal list. It keeps everything and goes to
  the end of the new list. Moving it into your personal list makes it private
  to you; if it was assigned to someone else it becomes unassigned (the form
  warns you first). You can't move tasks into someone else's personal list.
- **Quick add** in each list (with a **Required** toggle and **More options**
  for all the fields), on the Dashboard (to My Tasks) and in the calendar's
  day panel (on that date).
- **Sort, filter and order.** Each list has **Upcoming | Completed**, a sort
  (manual, due date, priority, assignee, newest) and filters for assignee,
  priority, type, place and completion; your choice is remembered per list on
  that device. With manual sort and no filters, drag tasks to reorder them
  (Alt + ↑/↓ from the keyboard).
- **Completed tasks** are deleted automatically 60 days after they were
  completed; anyone can delete one sooner.
- **From Household Docs.** If the household also uses the **Household Docs**
  app, a checklist there can become tasks here: **⋯ → Make a Todo list** (or
  select items → **Send to Todo**) puts them in a new list — private to you or
  shared — or in a list you pick. Each item becomes a task at the end of the
  list, an indented item goes into the task's checklist, ticked items arrive
  completed, and every such task shows **from Docs**. Todo checks that you may
  add to that list, as if you did it here: shared lists and your own personal
  lists only. Due dates, assignees and reminders are then set here as usual;
  nobody is notified when the tasks arrive. You must have opened Household
  Todo at least once (and not be turned off in Admin → Users).

## Calendar

The start page. **Month** view is a grid of every dated task and every
schedule occurrence; **Agenda** lists the next 30 days (the default on a
phone). The week can start on Sunday or Monday. Filter by type and person,
**Show completed**, and choose which schedule items to show with the
**🗓 Schedule items** button (all, none, or tick off the ones you don't want
to see — remembered on that device; the items, reminders and sensors keep
working). If maintenance is on, its due dates show with 🔧, and the 🗓 button
can hide them.

Click a day for its panel: add a task on that date, see the day's tasks and
schedule items, and **Skip**, **Move…** or **Undo** a schedule date. An "N
overdue" banner links to the Dashboard, and a footer counts tasks with no
date.

## Dashboard

Quick add to My Tasks; tiles for **Overdue**, **Due today**, **Next 7 days**
and **Open tasks**; **Coming up** (schedule items that are on now, and your
own items today or tomorrow); **workload cards** for each person (open,
overdue, due soon, done this week) plus *Unassigned*; and the overdue, today
and upcoming task groups. An admin can click someone's card to act as them.

## Schedule

Recurring things that are never "completed" — trash day, recycling every
other week, a yoga class. Rules: daily; weekly on chosen weekdays; every N
weeks; monthly on a day; every N months on a day; every N years; the Nth (or
last) weekday of the month; every N days.

- **Single dates.** **Skip** a date, **Move…** it to another day, or **Add
  extra date…**, without touching the rule, each with an optional reason.
  **Undo** removes the change. Changing the rule or first date clears skips
  and moves (you're asked first).
- **For one person.** **For** can be *Household* (the default) or one
  person. Give it a **Start** and **End** time (both or neither) and a
  **Place** and it becomes an appointment, e.g. "Yoga, every Tue, Thu & Fri,
  18:00–19:00 @ Studio". Items for you are in your reminders (see below).
- **Private.** On an item that's for you, **Only visible to me** hides it from
  everyone else — Schedule tab, calendar, dashboard and notifications. Only
  you can change or delete it (an admin only while acting as you). Household
  items, assigned or not, can be edited by anyone.
- **Link**, **notes** and an **icon** (a preset or any `mdi:` icon name).
- The tab has an **All · Mine · Household** filter; each row shows the next
  date, person, time, place, drive time (if on), link and its Home Assistant
  entity, and **Next dates ▾** lists the coming dates with their changes.
- Up to 100 schedule items, and 50 date changes per item.

### Schedule sensors in Home Assistant

With **Expose schedule items to Home Assistant** on (App settings, on by
default) and the item's own **Publish to Home Assistant** switch on, each
item appears as `binary_sensor.household_todo_<name>` (the name part is fixed
when the item is created).

- An **all-day** item's sensor is **on** from *lead days* before a date until
  the end of that day ("1 day before" = on Sunday and Monday for a Monday
  pickup).
- A **timed** item's sensor is on **only from its start to its end time**
  (within about a minute).
- Attributes: `next_date`, `days_until`, `occurs_today`, `lead_days`, `rule`,
  `skipped_dates`, `extra_dates`, `assigned_to`, `start_time`, `end_time`,
  `next_start`, `next_end`.
- A **private** item starts with *Publish* off, because anyone who can use
  Home Assistant can read sensors; the form warns you if you turn it on.
- Sensors are re-published when something changes, when the date rolls over,
  and every *Sensor refresh* minutes, so they come back by themselves after a
  Home Assistant restart. Deleting an item or turning publishing off removes
  the entity.

Example automation — bins out the evening before:

```yaml
alias: Bins out tonight
trigger:
  - platform: state
    entity_id: binary_sensor.household_todo_trash_pickup
    to: "on"
condition:
  - condition: state
    entity_id: binary_sensor.household_todo_trash_pickup
    attribute: occurs_today
    state: false
action:
  - service: notify.mobile_app_my_phone
    data:
      message: "Trash pickup is tomorrow — bins out tonight."
```

## Places

A shared address book: name, address and an optional phone number. Pick a
place on any task or schedule item (the picker has a **Search places** box
and **+ New place…**). Each place has **Open in maps**, **Copy address** and
a 📞 call link; a task's 📍 chip opens maps too. The same address can't be
saved twice. Anyone can add, edit or delete a place (tasks just lose the
reference).

### Drive times (optional, off by default)

With **Drive times** on (Admin → App settings) and a home address set, each
place gets an estimated drive time from home. A task or schedule item with a
place and a time then shows **🚗 18 min · leave by 14:12**, and reminders add
"~18 min drive, leave by 14:12". The Places tab shows "~18 min from home" (or
"not calculated yet") and a **🔄 Recalculate drive time** button.

- Estimates are worked out in the background (one place a minute), cached,
  and refreshed about once a month or when an address changes; nothing waits
  on the internet.
- **Avoid toll roads** (on by default) uses a route without tolls, falling
  back to the fastest route when there isn't one ("uses toll roads" on the
  Places tab).
- An address with a suite, unit or apartment number is retried without it
  if the full address isn't found.
- Turning Drive times off hides all of this and stops every lookup; cached
  estimates stay in the database and come back if you turn it on again.

See *Where your data goes* for what this sends.

## Maintenance (optional)

House upkeep in its own 🔧 **Maintenance** tab, off until an admin turns it
on in **Admin → Maintenance** (which also creates a shared **Maintenance**
list and makes every admin a recipient).

- **Needs doing** — overdue items and items due within their reminder time.
  **✓ Done** records the date, a note, an optional cost and photos or a
  receipt, and works out the next due date. **💤 Snooze** moves one due date.
- **Coming up** — the next 90 days, by month.
- **Open jobs** — one-off jobs, which are ordinary tasks in the Maintenance
  list (it can't be deleted while Maintenance is on). Jobs can have files.
- **Suggestions** — about 45 common jobs (furnace filter, gutters, smoke
  alarms, dryer vent…), filtered by the **home profile** (what the house
  has). This season's come first; seasons follow Home Assistant's location,
  so the southern hemisphere works too. **+ Add** pre-fills the form; **Not
  for us** hides one. Admins can add their own suggestions.
- **All upkeep** — every item by category, with details, files, history,
  **Pause**, **Edit** and **Delete**.
- **History** — every Mark done, kept for good, with a yearly cost total and
  **Export CSV**. In the CSV, an item name, category, name, note or file name
  that starts with `=`, `+`, `-` or `@` gets a `'` in front, so a spreadsheet
  shows it as text instead of running it as a formula. **Undo** takes back an
  item's latest Mark done.
- An item repeats either **after it's done** (e.g. 3 months after the filter
  was changed) or **on set dates** (e.g. every 1 April and 1 October).
- **Notifications:** one message at each person's daily time listing what's
  due soon, due today and overdue (overdue repeated every 3, 7 or 14 days, or
  never), unassigned jobs due today, and new seasonal suggestions. Admins pick
  who is told; each item can choose its own people, and its assignee is always
  told. Anyone can turn maintenance notifications off for themselves.
- **Home Assistant:** an optional `sensor.household_todo_maintenance_overdue`
  (how many are overdue) and an optional per-item binary sensor that is on
  while the item is due or overdue.
- **Files folder:** an admin picks a folder inside `/share` in App settings.
  Files (up to 25 MB each; photos get a preview) are stored there by item and
  date; deleted files wait 30 days in `_deleted`. The folder is checked at
  start-up and every 5 minutes, and changing it never moves files.

## Reminders

Reminders are sent through Home Assistant's notify services. Each kind has
its own switch in **Settings → Reminders** (off until a person turns it on):

- **Daily digest** at your own time (**Send at**, 08:00 until you change it):
  your tasks due today (or up to 7 days ahead) and your overdue required
  ones.
- **Weekly summary** on a day and time you pick: the week ahead.
- **"N before" reminders** (up to 5, e.g. 2 hours and 15 minutes before) for
  your tasks that have a time, and for timed schedule items that are for you.
- **Assigned to you**: when someone else assigns you a task or schedule item.
- **Send test notification** checks it works.

"Your tasks" are tasks assigned to you or in your personal lists. Schedule
items that are for you (private ones included) are part of your reminders
only. A task or item with a link has it on the last line, and tapping the
notification opens it.

**Place details.** When a task or schedule item has a place, its reminder and
"assigned to you" notification show the place's address (📍) and phone (📞)
under the message, and on a phone with the Companion app add **Directions**
(opens the address in Google Maps) and **Call** buttons. The daily digest and
weekly summary add the address and phone under the item. An admin can turn
this off in **App settings → Reminders → Place details in reminders**; then
only the place's name is sent.

**Phones come from Home Assistant.** Set up a person's phone once in Home
Assistant: **Settings → People → (the person)** — **Allow person to login**
links the person to their user, and **Track device** picks their phone with
the Companion app. The app picks it up within 5 minutes (**Check Home
Assistant again** on Admin → Users does it now). Admins can add extra notify
services per person under **Admin → Users → Also** (a speaker, a second
phone), chosen from Home Assistant's list or typed, and press **Send a test**.
People can't choose their own service, so nobody can send to someone else's
device. If a send fails, the log shows Home Assistant's reason and the
closest real service name.

## Settings (everyone)

- **Reminders** (above; only as yourself, not while acting as someone).
- **How the app sees you** — the user name and user id Home Assistant sent
  (copyable), whether you're an admin, how many names are in `admin_users`,
  whether a reminder service is linked, and what to do if something's wrong.
- **Task types** — add, rename, recolour or delete types, each with an emoji.
- **Theme** (Midnight, Slate, Daylight, or Auto, which follows your device's
  light or dark setting) in the sidebar, or in Settings on a phone. If you had
  picked Ink before, you now get Midnight.

## Admin (admins only)

**🛡️ Admin** in the sidebar, with tabs **App settings | Users | Maintenance |
Storage**. Anyone else who opens an admin link sees "Only admins can open
this page", and the server refuses them either way.

- **Acting as.** An admin can pick *Acting as → someone* at the top of the
  page. Everything they add or change is then recorded as that person, and
  they can see that person's private lists and items. A banner shows while it
  is on, and each change is written to the app's **Log** tab.
- **Users.** Everyone who has opened the app: enable or disable a person
  (disabled people can't be assigned new tasks; their data stays), see their
  phones from Home Assistant, add extra notify services, send a test.
- **Maintenance.** Turn maintenance on, who is told, the home profile, the
  overdue sensor and your own suggestions.
- **Storage.** Download a backup and restore one (see *Backups*).
- **Connected apps** (under App settings, read only): the other household
  apps this one exchanges messages with (for example Household Docs), their
  version, when they were last heard from and what they can do. An app not
  heard from for a day shows as *not seen lately*.

### App settings

Each group of settings is a card, with a line under each setting saying what
it does, its range and its default. Changes are kept until you select
**Save** at the bottom (it shows how many unsaved changes there are);
**Discard changes** puts everything back. A number out of range is flagged at
the field before anything is saved.

| Setting | Default | What it does |
| --- | --- | --- |
| Expose schedule items to Home Assistant | On | Publishes each schedule item as a binary sensor. Off removes them all at once. |
| Sensor refresh (minutes) | 5 | How often every sensor is re-published (1–1440). |
| Drive times (uses OpenStreetMap services) | Off | Estimated drive times from home and "leave by" times. Off: nothing is looked up or sent, and no drive times are shown. |
| Home address | blank | Where drive times are measured from. Only used while Drive times is on. |
| Routing server (OSRM) | `https://router.project-osrm.org` | An OSRM-compatible server. Blank = the public default. |
| Address lookup server (Nominatim) | `https://nominatim.openstreetmap.org` | A Nominatim-compatible server. Blank = the public default. |
| Avoid toll roads | On | Estimate on routes without toll roads when there is one. |
| Maintenance files folder | blank | A folder inside `/share` for maintenance files. Blank = attaching files is off. |

Maintenance's own settings are on **Admin → Maintenance**. The daily reminder
time isn't an App setting: each person picks their own.

### The app option (Configuration tab)

| Option | Default | What it does |
| --- | --- | --- |
| `admin_users` | empty | Home Assistant user names or user ids (not case-sensitive; display names don't count) who can open Admin and act as others. Restart the app after changing it. |

## Who can see what, and where your data goes

- Shared lists, places, task types and household schedule items are visible
  to everyone who can open the app. Personal lists and private schedule
  items are visible only to their owner (and to an admin while acting as
  them).
- **Home Assistant** gets notifications (task titles, item names, times,
  place names and links, and the place's address and phone unless **Place
  details in reminders** is off — never notes) and, if you publish them,
  the schedule and maintenance sensors, which every Home Assistant user can
  read.
- **Drive times (only when turned on).** Your home address and every saved
  place's address are sent to the address lookup server
  (`nominatim.openstreetmap.org` by default) to be turned into coordinates,
  and pairs of coordinates (home → place) to the routing server
  (`router.project-osrm.org` by default). These are free public
  OpenStreetMap services with their own usage and privacy policies; the app
  stays well inside their limits (at most one lookup a second, one place a
  minute in the background, results cached for a month), and the OSRM demo
  server has no uptime guarantee. Nothing else — no task titles, notes or
  names — is sent. To keep addresses on your own network, run your own
  Nominatim and/or OSRM server and enter its address in App settings.
- **Messages between the household apps.** Household Docs and Todo talk
  over Home Assistant's event bus (`household_apps` events): ids, names,
  list names and checklist item texts, never anything else from Docs. Home
  Assistant admins and automations can see them, and Home Assistant's
  history keeps them unless you leave them out with this in Home Assistant's
  `configuration.yaml` (then restart Home Assistant):

  ```yaml
  recorder:
    exclude:
      event_types:
        - household_apps
  ```

- Nothing else leaves your Home Assistant. The app has no port on your
  network: it is reachable only through Home Assistant's sidebar (ingress),
  and it refuses requests that don't come through it.

## Backups

- **Admin → Storage → Download backup** saves a complete copy of the database
  (everyone's lists, tasks, places, schedule, maintenance records and the App
  settings).
- **Import** replaces everything with an uploaded backup — no merge, no undo.
  The file is checked first; a file that isn't a Household Todo backup is
  refused. Older backups are upgraded on import. The current maintenance
  files folder is kept.
- Home Assistant's own backups of the app include the database too. Maintenance
  **files** are not in the app's backup: include **Share** in Home
  Assistant's backups.

## Limits

- 100 schedule items, 50 date changes per item, 200 maintenance items.
- Titles up to 200 characters, notes up to 5,000, checklists up to 100 items,
  links up to 2,000 characters (`http://` or `https://` only).
- From Household Docs: list names up to 60 characters, items up to 200
  characters; a long checklist arrives in several parts.
- Up to 5 "N before" reminders per person, 10 extra notify services per
  person, files up to 25 MB.
- Completed tasks are removed after 60 days.
- Dates and "today" follow Home Assistant's time zone.

## Troubleshooting

- **"No admin yet" on every page** — add the user name it shows to
  `admin_users` in the app's Configuration tab, save and restart the app.
- **I'm in `admin_users` but not an admin** — check **Settings → How the app
  sees you**: use the user name or user id shown there (not the display
  name), and restart the app after saving.
- **No reminders** — check **How the app sees you → Reminder service linked**,
  the person's phone in Home Assistant (Settings → People → Track device),
  that the reminder kind is switched on in Settings → Reminders, and use
  **Send test notification**. A person has to open the app once to appear on
  Admin → Users.
- **"notify.… failed (HTTP 400)" in the log** — the service name matches
  nothing in Home Assistant; the log suggests the closest real name. Fix it
  on Admin → Users.
- **No drive times** — turn on **Drive times** and set a home address in App
  settings. If the address can't be found on the map, the log says so; fix
  it (it is retried hourly). **Recalculate drive time** tries one place now.
- **Wrong "today"** — the app reads Home Assistant's time zone at start-up;
  if it can't, it uses UTC and says so in the log. Restart the app after
  fixing Home Assistant's time zone.
- **Maintenance files "not connected"** — the folder is missing, read-only
  or belongs to another install; the page says which, and **Use this
  folder** sets it up again.
