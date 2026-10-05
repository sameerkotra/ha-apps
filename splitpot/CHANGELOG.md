# Changelog

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
