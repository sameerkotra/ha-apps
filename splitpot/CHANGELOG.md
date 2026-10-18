# Changelog

## 2.7.0

- **Summary** replaces the Dashboard: the overall balance as of today, and spending for this month, last month,
  this or last week, this year or any date range — narrowed by amount, who paid and group — with totals **by group**,
  **by person** (what each paid, their share, and the difference) and the transactions themselves.
- **Activity** is its own page: search the activity log by who made the change, group, days, amount (for expenses
  and payments) and words. Nothing loads until you search; results come 50 at a time with **Load more**.
- Activity entries about an expense or payment now keep their amount (older ones get it from their message, once).

## 2.6.2

**Security**
- **Only a group's members (and admins) can change who is in it.** Anyone could add people to any group before. Everyone
  still sees every group and can add expenses to it — Splitpot is built for one household that shares its costs
  openly, and the documentation now says so plainly; the Household Assistant is the narrower view (only your own groups).
- **Your default group is your own.** Starring a group used to change where Splitpot opens for the whole household; now
  it only changes it for you. A group that was the household default stays everyone's default until they pick their own.
- **Edits are written out in full and the people affected are told.** The activity log now says exactly what an edit
  changed — who paid, the split, the amount, the date — and when an edit changes who paid or someone's share, those
  people get a notification (when notifications are on), like a new charge.
- **Limits, generous by default** (Admin → App settings → Limits): 200 groups per person, 20,000 entries per group and
  60 changes per person per minute; 0 switches each one off. Group names are up to 80 characters and one request can
  name at most 50 people. Requests are capped at 1 MB (a restore at 512 MB), counted even without a Content-Length.
- **Disabled people can't be put into new charges or payments.** Editing an entry they're already in still works.
- **The fonts come from Splitpot itself** instead of Google Fonts, so the page loads nothing from the internet.
- The app's image is pinned to one Alpine release (`python:3.12-alpine3.22`, like the other household apps), with
  plain, pinned uvicorn instead of `uvicorn[standard]`.

**Fixed**
- **Amounts with more than two decimals are refused** instead of being rounded one way for the total and another for a
  percentage split, and the smallest amount is 0.01.
- **Balance sensors no longer linger**: a renamed person's old sensor, and the sensors of people whose last group was
  deleted, are removed from Home Assistant.
- The Household Assistant asks which group you mean when two of your groups have the same name, uses your own default
  group, and leaves disabled people out of an equal split.
- After a change, the ledger keeps every entry you had loaded, even past 500.
- Starring a group shows an error if it fails; recording a payment no longer shows a group you have just left; the
  Groups list and the delete confirmation count expenses and payments the same way.

**Changed**
- **Rename a group, take someone out of it, and change a settle-up payment** (✎ Name and members, and Edit on a
  payment's row). Someone can be taken out only once their balance in that group is zero.
- **Faster big groups and Dashboard**: the ledger's order is stored and indexed (worked out once for existing entries
  when the app starts), so pages, weeks and months are quick lookups; "Show each person's balance" is only worked out
  while it is ticked; overall balances are two sums instead of one query per expense.
- Balance sensors are updated just after the answer is sent, and only the ones whose balance changed (the background
  sync still re-sends all of them).
- The Users list only writes Home Assistant's people when it has read them again, not on every page load.
- The activity log keeps a year by default (**Keep the activity log**; 0 keeps everything).
- **Shared code**: an optional **App-messages signing key** (Admin → App settings → Household Assistant; the same key
  in every household app) signs the messages between the apps; API answers are never cached by the browser, while the
  page's scripts, styles and fonts are cached until the next version, so it loads faster; database writes are lighter.

## 2.6.1

- **Load more shows the balances too**: with **Show each person's balance** ticked, the rows added by **Load more**
  now show everyone's balance under them, like the first page.
- **Settle-up rows fit on phones**: the "Ann → Ben $20.00 Record payment" rows no longer spill past the edge of the
  screen; on a phone, **Record payment** sits on its own line under the names and amount.

## 2.6.0

- **Each person's balance on every ledger entry**: tick **Show each person's balance** above a group's ledger to see,
  under each expense and payment, everyone's balance in the group just after it (+ is owed, − owes) — how the group
  got to where it is. Off by default, as before; your choice is remembered on that device.

## 2.5.1

- **Equal splits to the cent**: an amount split equally now gives everyone the same share, with the leftover cents going one each to the first people — 100 between 6 is 16.67, 16.67, 16.67, 16.67, 16.66, 16.66 instead of the last person getting 16.65, and a tiny amount can no longer leave someone with a negative share. The Household Assistant's "I paid 40 for groceries…" splits the same way.
- **Custom amounts must add up exactly**: a split by amount that was a cent or two off the total was saved as typed, so the group's balances didn't quite add up. It now says how much it's short or over.
- **One balance sensor per person**: people whose names give the same sensor name ("Asha K" and "Asha-K", or names without Latin letters) shared one sensor in Home Assistant, each overwriting the other. Each now gets their own (the later ones with a short ending); everyone else's sensor keeps its name.
- **Ledger in the right order**: an entry with a picked date and one added "today" are now ordered by when they happened in Home Assistant's time zone, in the group and on the Transactions page.
- The Transactions page answers a clear error for a week or month too far away instead of a server error.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 2.5.0

- **Add an expense with the Household Assistant**: "I paid 40 for groceries, split with Meera" — split equally between the people named (everyone in the group when nobody is), only after you tap the exact change it proposes. It's checked like the Add expense form, shows in the activity as added with the Household Assistant, and notifies people as an added charge does.

## 2.4.3

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- A group's CSV file name has Home Assistant's date, not UTC's.
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 2.4.2

- The Household Assistant's answers link straight to each group they mention, not just to Splitpot.

## 2.4.1

- **Notifications open the group for everyone**: tapping a new-charge or payment notification opened the admin's Settings → Apps page — or nothing, for anyone who isn't an admin — and lost the group. It now opens Splitpot's own sidebar page on that group (`/<page>/group/<id>`).

## 2.4.0

- **Can answer the Household Assistant**: once an admin turns on **Answer the Household Assistant** (Admin → App settings; off by default, because money is private), the new Household Assistant app can ask Splitpot "Who owes me money?" or "What did we spend on the trip?" — balances and the newest 20 entries of **only the groups that person is in**, matched by their Home Assistant login — with a link back to Splitpot. Each person can turn it off for themselves on **My settings**.
- Splitpot now joins the household apps' message bus (Home Assistant's event bus). The **DOCS** show how to keep those messages out of Home Assistant's history.

## 2.3.0

- **Notifications for new charges**: when someone adds a charge, everyone in it (who paid and everyone with a share) gets a phone notification through Home Assistant — who added what, the amount, the group and their share; tapping it opens the group. Settle-up payments can notify both people too. Off until an admin turns on **Notify people about new charges** in Admin → App settings → Notifications; each person can turn off **Receive notifications** on the new **My settings** page. Only people linked to a Home Assistant login with a phone or an extra notify service are notified.
- **Admin → Users** now has phones from Home Assistant, extra notify services and **Send a test**, like the other household apps.
- **Group ledger loads 20 at a time**: a group opens with its newest 20 entries and a **Load 20 more** button, so big groups open quickly. Balances, totals and the CSV export still cover every entry.
- On a phone, ledger rows wrap so the amount and buttons sit under the text.

## 2.2.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.2.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 2.2.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. Paper is now Midnight; your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- **Export CSV**: the per-person share column headers are now protected against spreadsheet formulas too (a leading `'` when a name starts with `=`, `+`, `-` or `@`).
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.1.0

- **Export a group to CSV.** **⬇ Export CSV**, beside *Delete group*, downloads every expense and settle-up payment in the group, oldest first: date, type, description, amount, currency, who paid, how it was split, and each member's share in its own column. Anyone can export; it opens in any spreadsheet app.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Groups for a household, a trip or a project, with an optional default group as the start page
- Expenses split equally, by exact amounts or by percentages, always adding up to the cent
- Per-group balances with the fewest "who pays whom" transfers, and recorded settle-up payments
- Dashboard with overall balances, weekly and monthly spending and an activity log
- Everyone signs in with their Home Assistant account; people come from Home Assistant's Persons
- Optional balance sensors in Home Assistant, one per person
- Admin area for app settings (currency, sensor sync), users and database backup/restore
