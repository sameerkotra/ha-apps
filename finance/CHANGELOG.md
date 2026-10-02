# Changelog

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
