# Changelog

## 1.2.2

- **Your own Household Assistant switch**: while an administrator lets the Household Assistant ask, the **Who am I** page has **Let the Household Assistant answer for me**. Turn it off and the assistant won't answer your questions from Finance.

## 1.2.1

- **Links open the right page**: the Household Assistant's answers open that month's dashboard or Recurring. Links ending in `/month/<YYYY-MM>`, `/dashboard` or `/recurring` open that page.

## 1.2.0

- **Can answer the Household Assistant**: once an admin turns on **Answer the Household Assistant** (Admin → App settings; off by default, because money is private), the new Household Assistant app can ask Finance about a person's own month — income, spending, net and top categories, a category's largest charges (never notes), recurring charges and bills coming up — and about the data an admin shared with them. Each answer links back to the app.
- Finance now joins the household apps' message bus (Home Assistant's event bus); its small tables are kept out of Query and Reports. The **DOCS** show how to keep those messages out of Home Assistant's history.

## 1.1.1

- **Security**: PDFs are now read by the PDF tools (poppler) as a separate user without any rights, with memory, time and file-size limits, on a copy of the file — they can't reach the app's database or other files.
- Cross-site form posts are still refused, now by the check every household app shares. No visible change.
- Backups still leave out the AI access key; restoring keeps the key this install has (now the same shared code in every app that has access keys).
- `config.yaml` now spells out the security settings like the other apps (AppArmor on; no Supervisor, sign-in or Docker API access). These were already the defaults, so nothing changes.

## 1.1.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device), as in the other household apps; Sandstone is now Daylight and your saved choice carries over.
- **How the app sees you** shows your display name too, and the **No admin yet** banner uses the same wording as the other household apps.
- **Security**: pages now send a Content-Security-Policy and `nosniff` header; scripts moved out of the pages into files. No visible change.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 1.0.1

- **Tolls are no longer tied to one road.** The optional toll feature is now called **Tolls** everywhere (menu, pages, App settings → Features, uploads, recently deleted), and statements are read as general toll road statements that list passes under a tag and plate. Nothing about your data changes.
- When Home Assistant's time zone can't be read, the fallback is now UTC instead of a fixed US zone.

## 1.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- Bank and credit-card statements (PDF or CSV) read by the AI model you choose: your own Ollama, an OpenAI-compatible service or Anthropic Claude, checked against the balances printed in the PDF before you confirm.
- Transactions with search, categories and your own rules, notes, exclusions and CSV export.
- Transfers between your own accounts linked automatically; recurring charges detected from your history.
- Home dashboard with income and spending by category, and Compare for two months or years side by side.
- Saved reports with filters, charts and CSV; the admin writes them in SQL or asks the AI to write the SQL.
- Per-person data inside Home Assistant, with optional shared access set by the admin.
- Admin tools: database backup and import, recently deleted items, AI usage and cost totals, App settings.
- Optional utility bills and toll statements, switched on under Admin → App settings → Features.
