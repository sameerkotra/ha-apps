# Splitpot

Splitpot is a shared-expense splitter for a household, in the spirit of
Splitwise, that runs as a Home Assistant app. You open it from the Home
Assistant sidebar and it already knows who you are from your Home Assistant
login. You make groups, log who paid for what and how it's shared, and
Splitpot keeps everyone's balance, suggests the fewest payments to settle
up, and can show each person's balance as a Home Assistant sensor.

Everything is stored on your Home Assistant. Splitpot has no accounts of its
own, no cloud service and no published port.

## Getting started

1. In Home Assistant open **Settings → Apps → Install app**, click the
   **⋮** menu (top right) → **Repositories**, paste
   `https://github.com/sameerkotra/ha-apps`, click **Add** and close the
   dialog.
2. Find **Splitpot** in **Settings → Apps → Install app** (reload the page if
   it isn't listed yet) and click **Install**. The first install builds the
   app on your device, which can take a few minutes on a small machine such
   as a Raspberry Pi.
3. On the app's **Information** tab turn on **Show in sidebar** and click
   **Start**.
4. Open **Splitpot** from the sidebar. A fresh install shows a **No admin
   yet** banner at the top of every page, with your Home Assistant user name
   in it.
5. Go to the app's **Configuration** tab, add that user name to
   **Administrators** (`admin_users`), click **Save**, and **restart** the
   app (**Information** tab → **Restart**). Not sure which name to use? Click your name at the bottom of
   Splitpot's sidebar (or 👤 on a phone): **How the app sees you** shows the
   exact values.
6. Reopen Splitpot. The banner is gone and you have an **Admin** item (shield
   icon) in the sidebar. Check **Admin → App settings**, mainly the
   **currency** (USD by default).
7. Make sure everyone who should use Splitpot is a Person in Home Assistant
   (**Settings → People**) with **Allow person to login** turned on and
   linked to their user. Then create your first group under **Groups**.

Nobody becomes an administrator automatically, not even the first person to
open the app.

## People and signing in

- **There's no "add user" screen.** Everyone Splitpot can put in a group or an
  expense comes from Home Assistant's **Persons** (Settings → People), and
  only Persons linked to a Home Assistant user, so pets and device-only
  trackers don't appear. The list refreshes whenever the app opens. If
  someone is missing, add or link them in Home Assistant and reopen
  Splitpot.
- **Renaming** someone in Home Assistant renames them in Splitpot too.
  Removing someone from Home Assistant doesn't delete anything: their past
  expenses, balances and group memberships stay, they just aren't offered for
  new ones.
- **Acting as** (top right) starts as you: Splitpot finds the Person linked to
  your Home Assistant login. If it can't (for example the Person isn't linked
  to your user yet) it falls back to your display name, but only if exactly
  one enabled person has that name. You can switch Acting as to log
  something on someone else's behalf. It only changes who is preselected as
  **Paid by**; it doesn't change what you're allowed to do.
- **How the app sees you**: click **Signed in as …** at the bottom of the
  sidebar (or 👤 at the top on a phone). It shows the user name, user id and
  display name Home Assistant sent, whether you're an administrator, and how
  many names are in the administrators list (only the count, never the
  names), plus what to do if you expected to be an administrator. It always
  shows the real signed-in person, whatever Acting as is set to.
- **Disabled users**: an administrator can disable someone in **Admin →
  Users**, for example a person who moved out. They disappear from every
  picker (Acting as, Paid by, group members) but their history and balances
  stay exactly as they were, and Home Assistant's sync never re-enables them.
  Disabled members are shown as "(disabled)" on a group's page.

## Groups

- **Create a group** under **Groups**: give it a name (up to 60 characters)
  and tick the people in it. Members are chosen when the group is created.
- The **Groups** list shows each group's members and how many expenses it
  has (settle-up payments aren't counted).
- **Default group**: click the star (☆) next to a group, or **☆ Set as
  default** on the group's page. Splitpot then opens straight into that group
  instead of the Dashboard. There is one default for the whole household;
  marking another group moves it, and clicking the star on the current
  default clears it.
- **Delete group** (administrators only, top of the group's page) removes the
  group with all its expenses and payments, after a confirmation that says
  how many go with it. This can't be undone; the activity log keeps a record
  of it.
- **⬇ Export CSV** (everyone, top of the group's page) downloads every
  expense and settle-up payment in the group, oldest first, as a CSV file for
  a spreadsheet: date, type, description, amount, currency, who paid, how it
  was split, and one "<name> share" column per person. A description, name or
  column heading that starts with `=`, `+`, `-` or `@` gets a `'` in front, so
  a spreadsheet shows it as text instead of running it as a formula.

## Expenses

Open a group and use **Add an expense** on the left:

- **Description** (what it was for), **Amount**, **Paid by** (starts as the
  Acting as person when they're in the group) and **Date** (today by default;
  pick an earlier day for something you forgot. Dates can't be in the
  future). An earlier date files the expense under that day's week and month
  on the Dashboard.
- **Split**: choose who shares the expense by ticking people, then how:
  - **Equal**: the amount is divided evenly to the cent; any leftover cent
    goes to the last person, so the shares always add up exactly.
  - **Custom → By amount**: type each person's share. The shares must add up
    to the expense amount (a cent or two of rounding is accepted). People
    with 0 are left out.
  - **Custom → By percentage**: type each person's percentage (up to 2
    decimals). Each row shows that person's amount, and a line underneath
    shows "Total … · remaining …", red until it is exactly 100%. **Split the
    rest evenly** fills the rows you left empty with what's left of 100%.
    The percentages must add up to exactly 100%. Amounts are worked out to the
    cent and always add up exactly to the expense; a leftover cent goes to
    whoever's share was rounded down the most. 0% rows are left out. The
    ledger shows e.g. "split by percentage · Ann 60% ($30.00) · Ben 40%
    ($20.00)".
- **Edit**: every ledger row has an **Edit** button. The form loads the
  expense (a percentage split opens with its saved percentages); change
  anything, including switching between equal, amounts and percentages, and
  click **Save changes**, or **Cancel**. Balances update straight away.
- **Delete** (administrators only) removes an expense after a confirmation.
- **The ledger** lists the group's expenses and payments, newest first, with
  date, payer and how it was split. Each payer has their own colour (the name
  and a coloured left edge on the row), so you can see at a glance who paid
  what. The Dashboard's transaction list uses the same colours.
- **Load more**: a group opens with its **latest 20** ledger entries, so a
  long history doesn't slow the page down. Under the list, "Showing 20 of 65
  entries" and **Load 20 more** fetch the next older ones; repeat until
  everything is shown. Balances, the delete confirmation's count and **Export
  CSV** always cover every entry, however many are showing. After you add,
  edit, delete or record a payment the list keeps as many entries as you had
  loaded.

Anyone can add and edit expenses and record payments. Only administrators
can delete. Every change, including each edit and delete, is written to the
activity log with who made it.

## Balances and settling up

- **Balances** (right side of a group's page) shows each member's net
  position in that group (who is owed, who owes) and the suggested transfers
  that settle everything with as few payments as possible.
- **Record payment** next to a suggested transfer asks how much was paid; the
  full amount is filled in, and you can change it for a partial payment. The
  payment appears in the ledger as "💸 Ann paid Ben · settle-up payment" and
  moves the balances as if that money had been paid back, so a full payment
  brings both people to zero.
- Payments are not spending: they're left out of the Dashboard's totals and
  transaction list. They can't be edited; an administrator can delete one to
  undo it.

## Dashboard

The first item in the sidebar:

- **Tiles**: number of groups and users, and the total spent this week and
  this month.
- **Overall balance**: each person's net position summed over all their
  groups ("is owed", "owes", "settled up"). This is not a payment suggestion,
  because people in different groups may never have shared an expense; use
  each group's Balances to settle up.
- **Transactions**: switch between **Week** and **Month** and step back with
  ← / →. You see the total and number of expenses, a bar chart (by day of the
  week, or by group for a month) and the list of expenses with date, group
  and payer. Weeks start on Monday. Weeks and months follow Home Assistant's
  time zone.
- **Activity log**: the latest 50 changes (groups created, members added,
  expenses added/edited/deleted, payments, default group, users disabled or
  enabled, settings changed), newest first, with who did it and when.

## Phone notifications

When an administrator switches on **Notify people about new charges** (Admin →
App settings → Notifications; off on a new install and after an update),
Splitpot sends a phone notification through Home Assistant whenever someone
adds a charge, to **everyone in it** — whoever paid and everyone with a
share — **except the person who added it**, e.g.

> Asha added 'Dinner' ($90.00) to Trip, paid by Ravi. Your share: $30.00.

Tapping it opens Splitpot on that group. With **Also notify settle-up
payments** (on unless switched off) the two people in a recorded settle-up
payment are told too ("Ravi paid you $25.00 in Trip"), again not whoever
recorded it. Editing or deleting an entry never sends a notification; the
activity log shows those.

- **Which phones**: the ones Home Assistant links to the person — Settings →
  People → the person → **Track device**, picking their phone with the Home
  Assistant Companion app — plus any extra notify service an administrator
  adds under Admin → Users. A person whose Home Assistant login isn't linked
  (Settings → People → Allow person to login) or who has no phone gets
  nothing.
- **Turning them off for yourself**: **My settings** (bell icon in the
  sidebar) → **Receive notifications**. It's matched to you by your Home
  Assistant login.
- Notifications are sent in the background: adding a charge never waits for
  them, and if Home Assistant can't be reached nothing fails — the app just
  notes it in its log.
- The text (names, description, amounts, group) is handed to Home Assistant,
  which delivers it to the phone the way it delivers its own notifications
  (for the Companion app, through its push service).

## The Household Assistant

If the household also uses the **Household Assistant** app, you can ask it
"Who owes me money?" or "What did we spend on the trip?". Splitpot tells it, for
**only the groups you're in**:

- **balances** — who owes whom after the simplest settle-up, and where you stand
  overall;
- **recent expenses** — the newest 20 entries: what, who paid, how much and your
  share (optionally one group, or the last few days).

Money is private, so this is **off until an admin turns it on** (**Admin → App
settings → Household Assistant → Answer the Household Assistant**). You can also
turn it off for yourself on **My settings → Let the Household Assistant answer
for me**. You're matched by your Home Assistant login, never by name; a person
whose login isn't linked gets no answers.

The answers travel through Home Assistant's event bus, which Home Assistant's
recorder keeps in its history unless told not to. Add this to Home Assistant's
`configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

## Look and navigation

- **Theme**: Midnight (dark, the default), Slate (dark blue-grey), Daylight
  (light) or Auto (Daylight when your device is set to light, Midnight when
  it is dark), at the bottom of the sidebar. The choice is remembered by your
  browser; if you had picked Paper before, you now get Midnight.
- **Collapse the sidebar** to an icon rail with the ‹ button (desktop). On a
  phone the sidebar becomes a tab bar at the bottom.
- **Back in the Home Assistant app**: the back gesture first cancels an
  expense you're editing, then returns to the start page (your default group
  or the Dashboard); only Back on the start page leaves Splitpot.

## Balance sensors in Home Assistant

With **Sync balances to Home Assistant** on (Admin → App settings, on by
default), Splitpot creates one sensor per person who is in at least one
group:

- `sensor.splitpot_balance_<name>`: their overall balance across all groups
  (positive = they're owed money, negative = they owe), with the App
  settings currency as its unit and a "<Name> Splitpot Balance" name.

The sensors update right after every change and, in the background, every
few minutes (the sync interval). Use them on dashboards or in automations,
for example a notification when someone's balance passes a threshold. Like
the Dashboard's overall balance, this is a net position, not a "pay this
person" suggestion. No other entities are created.

Turning sync off removes the sensors from Home Assistant. They are created
through Home Assistant's API, so after a Home Assistant restart they come
back with the next sync. If someone is renamed, a sensor with the new name
appears; the old one lingers until sync is switched off or Home Assistant
restarts.

## Admin area

Administrators get an **Admin** item (shield icon) in the sidebar. For
everyone else it's hidden, opening its link says "Only admins can open this
page", and the server refuses its functions. It has three tabs.

### App settings

Household-wide settings, in four cards (Money, Home Assistant, Notifications
and Household Assistant), with a
line under each setting saying what it does, its range and its default. Your
changes are kept until you select **Save settings** at the bottom (it shows
how many unsaved changes there are); **Discard changes** puts everything back.
A number out of range is flagged at the field before anything is saved.
Saving applies them straight away, no restart. Changes are written to the
activity log.

| Setting | Default | What it does |
| --- | --- | --- |
| Currency code | `USD` | A 3-letter ISO 4217 code such as USD, EUR, GBP or INR (upper or lower case). Used for every amount in the app and for new activity log entries (older entries keep their text), and as the balance sensors' unit. The page shows a preview, e.g. "€1,234.50". |
| Sync balances to Home Assistant | on | Publishes the balance sensors described above. Off removes them from Home Assistant and stops updating them; on publishes them at once. If Splitpot has no connection to Home Assistant, a note says nothing is published. |
| Sensor sync interval (minutes) | `5` | How often the sensors are refreshed in the background, 1–60, on top of the instant update after every change. A new value applies from the next cycle. Greyed out while sync is off. |
| Notify people about new charges | off | Phone notifications for new charges (see *Phone notifications* above). |
| Also notify settle-up payments | on | The two people in a recorded settle-up payment are told too. Greyed out while the switch above is off. |
| Answer the Household Assistant | off | Lets the Household Assistant app tell people the balances and recent expenses of their own groups (see *The Household Assistant*). |

### Users

Everyone Splitpot knows from Home Assistant, with how many groups they're in,
their Home Assistant Person entity, and a **Disable/Enable** button (see
*Disabled users* above). For notifications each card also shows:

- **Phones**: the phones Home Assistant links to the person (read-only; set
  them up in Home Assistant), and a note when no person or phone is linked.
- **Also**: extra notify services for this person (a speaker, a second
  service): pick one Home Assistant offers or type a name like
  `notify.mobile_app_phone`; ✕ removes it.
- **Send a test**: a short test notification to all of them.

**Check Home Assistant again** re-reads the people and phones straight away
(otherwise every few minutes).

### Storage

- **Download database (.db)**: a complete, consistent copy of all of
  Splitpot's data (groups, expenses, payments, users, activity log, App
  settings) as one SQLite file, for a manual backup or to open in an SQLite
  tool.
- **Restore from backup**: upload such a file to **replace everything**
  currently stored (after a confirmation). There is no merge and no undo;
  it's meant for recovering onto a fresh install. A file that isn't a
  Splitpot database is refused without touching your data. A backup made by
  an older Splitpot is brought up to date automatically; if it has no App
  settings, your current settings are kept.

## App configuration

The app's **Configuration** tab in Home Assistant has one option; everything else is in
Admin → App settings.

| Option | Default | What it does |
| --- | --- | --- |
| Administrators (`admin_users`) | empty | Home Assistant user names (login names) or user ids, any case, of the people who may open the Admin area and delete expenses, payments and groups. Display names are not accepted: they aren't unique, so matching them could make someone an administrator just by taking an administrator's name. While the list is empty nobody is an administrator and every page shows **No admin yet**. The list is read when the app starts, so **restart** it after a change. |

If an entry only matches someone's display name, the app's log and that
person's **How the app sees you** page say which user name or id to list
instead.

## Who can see what, and where data goes

- **Everyone** who can log in to Home Assistant can open Splitpot
  (`panel_admin: false`) and see every group, expense, balance and the
  activity log, and add or edit expenses and payments. It's built for one
  household that shares its expenses openly. If there are Home Assistant
  accounts that shouldn't see this, Splitpot is not the right place for that
  data.
- **Administrators** (the `admin_users` list) additionally get the Admin area
  and can delete. The server enforces this; hidden buttons are only a
  convenience.
- **Access** is only through Home Assistant's Ingress. The app has no
  published port, and it refuses requests that don't come through Home
  Assistant's ingress proxy with a signed-in user.
- **Home Assistant**: Splitpot reads `person.*` entities, the people's
  Companion-app phones, the notify services and Home Assistant's time zone,
  writes the balance sensors and (when switched on) sends phone
  notifications, through the
  Supervisor's API (the app asks for `homeassistant_api` for this; Home
  Assistant doesn't offer a narrower permission). Anything that can read your
  Home Assistant states can read the balance sensors.
- **Outside your home network**: Splitpot's server sends nothing anywhere.
  The only request that leaves your network is your browser loading the two
  fonts (Source Serif 4 and Inter) from Google Fonts
  (`fonts.googleapis.com`, `fonts.gstatic.com`), which is a normal web font
  request and contains none of your Splitpot data. If those can't be reached
  the app uses your system fonts.
- **Storage**: one SQLite file, `/data/splitpot.db`, in the app's own
  storage. It isn't encrypted: anyone with access to the Home Assistant host
  or a copy of a Home Assistant backup can read it.

## Backups

Splitpot's data is in the app's own storage, so it's included in Home
Assistant's backups automatically (full backups, or partial backups that
include the Splitpot app). For an extra copy, administrators can use
**Admin → Storage → Download database (.db)**, and restore it on the same
page.

## Limits

- Members are chosen when a group is created; there's no screen to add or
  remove members later or to rename a group. Create a new group instead.
- Users can't be created or deleted in Splitpot; they come from Home
  Assistant Persons that are linked to a user.
- One currency for the whole app; no conversion between currencies.
- Amounts are between 0.01 and 1,000,000 with two decimals. Descriptions
  are up to 80 characters in the form. Dates must be from 2000 onwards and
  not in the future.
- Splitpot doesn't track who has actually paid outside the app; it only knows
  the payments you record.
- Deletes and restores can't be undone.
- Balance sensors exist only for people in at least one group.

## Troubleshooting

- **"No admin yet" won't go away**: the app reads `admin_users` only when
  it starts. Save the Configuration tab, then **restart** the app
  (**Information** tab → **Restart**). Use the
  user name or id exactly as **How the app sees you** shows it (case doesn't
  matter); display names don't work.
- **I'm listed but not an administrator**: open **How the app sees you**; it
  says whether the list is empty in the running app, whether only your
  display name matches, or which value to add.
- **Someone is missing from the pickers**: they need a Person in Home
  Assistant (Settings → People) linked to a Home Assistant user. Also check
  **Admin → Users** in case they're disabled. Then reopen Splitpot.
- **Acting as isn't set to me**: check that your Person in Home Assistant has
  your user set under **Allow person to login**.
- **"This week" looks off by a day**: Splitpot uses Home Assistant's time
  zone (Settings → System → General). If it can't read it at start, it uses
  UTC; the app's **Log** tab says so. Restart the app after fixing the cause.
- **No sensors in Home Assistant**: check **Sync balances to Home Assistant**
  in App settings, that the person is in at least one group, and the app's
  **Log** tab for warnings.
- **Something else**: the app's **Log** tab shows what Splitpot is doing,
  including any problem talking to Home Assistant.
