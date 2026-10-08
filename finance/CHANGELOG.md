# Changelog

## 1.3.0

- **More for the Household Assistant**: "How much did we spend at Costco this year?" (the total at one shop or company, each month's, the latest charges) and "What's the balance on my accounts?" (each account's balance from its latest statement; a card's is what's owed).

## 1.2.7

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it fell back to UTC — so in the Americas "today" turned into tomorrow in the evening. It now asks a few times over half a minute and otherwise uses the zone the Supervisor gives every app (Home Assistant's own).
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 1.2.6

- **Credit-card money in and out — the real cause**: the model had read the card statement the right way round all
  along. The statement's credit balance ("you're owed $14.01") was read as owing $14.01, and the check that catches a
  backwards reading then took the *right* reading for a backwards one and swapped every sign. Now a card's payment
  lines decide whenever there are any: payments that came out negative are right and are never swapped (payments
  that came out as charges still are), and a balance that points the other way is taken as a misread credit
  balance, so the statement still reconciles. Press **Re-extract** on the statement (or delete and upload it again
  if it was confirmed).

## 1.2.5

- **Credit-card money in and out, for real this time**: a card statement read backwards (payments as charges,
  purchases as payments) still came out swapped when the statement's credit balance was printed in a way the app
  read without its minus sign — the backwards reading then seemed to match the balances. The payments now decide
  first: card payments ("PAYMENT - THANK YOU", "AUTOPAY") that came out as charges always flip every sign, and the
  misread balance is put right too, so the statement still reconciles. A trailing minus ("$14.01-") is read as a
  credit as well. Press **Re-extract** on the statement (or delete and upload it again if it was confirmed).

## 1.2.4

- **Credit-card money in and out the right way round**: when the model read a card statement like a bank account
  (payments as charges and purchases as payments), the app only noticed if it could read the statement's previous
  and new balance — and a card that ended in credit ("-$14.01", "$14.01 CR", "($14.01)") was read as owing $14.01,
  so the backwards reading looked right. Credit balances are now read as credits, and when the balances can't
  decide, a card's payment lines ("PAYMENT - THANK YOU", "AUTOPAY") coming out as charges is enough to put every
  sign right (noted on the statement as *Signs flipped*). A statement already imported the wrong way round can be
  fixed with **Re-extract** while it waits for confirmation; once confirmed, delete it and upload it again.

## 1.2.3

- **Shared AI code**: the AI connection can offer tools to a model in its own way (used by the Household Assistant); nothing changes in this app.

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
