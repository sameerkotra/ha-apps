# Changelog

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
