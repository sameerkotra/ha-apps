# Changelog

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
