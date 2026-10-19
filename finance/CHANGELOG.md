# Changelog

## 1.7.0

- **Expected statement dates and overdue reminders.** Each account can now say when its statement is ready (Setup → Accounts → **Expected statement**: monthly, every 2, 3 or 6 months or yearly, on a day or the last day of the month). When nothing has been uploaded for it by then, its owner sees an amber notice on Home and Upload — with **Upload** (the account already picked) and **Dismiss** — and a count on the Upload menu item; the accounts lists get a **Next statement** column ("Oct 12", "Overdue 3 days"). Only the latest statement is ever shown, so a missed month never piles up.
- **My reminders** (new Setup → **Settings**, each person's own): choose **No notifications** or **Remind me** on 1 to 6 days after the expected date, keep reminding every week or two, at a time of day. Nobody is reminded until they choose. Reminders go to the phones Home Assistant links to the person, plus any extra **Notify** service an admin adds on Admin → Users; several statements due together come as one notification, which names the account only (never its number) and opens Upload when tapped. Only the account's owner is reminded, never people it's shared with.
- **Admin → App settings → Statement reminders** turns all of it off for everyone (on by default); every setting is kept.

## 1.6.0

- **QFX / OFX statements.** Your bank's "Download transactions → Quicken (QFX)" or "Money (OFX)" file can now be uploaded on Upload → Statements, next to PDFs (both kinds in one go is fine). It's read in a second, with no AI and no confirmation step, and becomes a statement like a PDF: a **QFX** badge and its dates ("Mar 3 – Jun 1, 2026") in Statement history, its closing balance, duplicates checked and rows categorized. Choose **Match by account number** to send each account in the file to yours with the same last 4 digits (a file with checking and savings together becomes two statements); when nothing matches you're offered to create the account, and when the account you picked ends in different digits you're asked first. Overlapping downloads ("the last 90 days" every month) only add the new rows, using the bank's own transaction ids; the rest are listed for **Insert anyway**. Credit card files are turned the app's way round. A statement whose opening balance doesn't follow on from the previous one waits in Review. Only the last 4 digits of an account number are ever kept.
- **Preferred upload method: QFX / OFX only.** Accounts can prefer QFX, the Upload page's reminder knows about QFX files (asked once when an upload mixes types), "Either" is now "Any", and an account created from a QFX file starts as "QFX only".

## 1.5.2

- **CSV import: lines above the header.** A bank export that starts with its own lines (name, card, time period of the report) before the Date / Description / … header now imports: the header is found among the top rows by itself. Under **Header and data rows** on the CSV form you can also see the file's first rows, tap the header row, or type the header row and the row the data starts on. If the header can't be found, you're shown the first rows to choose from instead of only an error. Row numbers in errors are the file's own.

## 1.5.1

- **Fixes to one person's data**: The Data dialogs, after a check in the browser: the ⋯ Data menu stays on screen (it opens the other way near an edge or a bottom bar), a wrong name is caught before the copy is downloaded, the copy uses the export's usual choices, the red button is readable in every theme, and the name box only shows when it's needed. Finance's own dialog style no longer squeezes the Data dialogs, and the Data button sits at the right of each person.

## 1.5.0

- **One person's data: export, import, delete** (Admin → Users → ⋯ Data on each person, and **Import a person's data…** above the list). Export downloads one file with everything they have (accounts, statements and their PDFs, transactions, CSV imports, rules, categories, utility bills, toll statements and toll setup); Import loads it into anyone — adding to what they have without doubling anything, or replacing it — with every row checked and new ids throughout; Delete removes their data for good (and, if you choose, them), unlike Wipe all data. Shared access, saved reports, App settings and the AI usage total stay out of it.

## 1.4.4

- **"I spent 200 on fuel at Shell"**: the Household Assistant can now add a spend by hand to one of your own accounts — the same row the Uploads page's *Add one by hand* makes, only after you tap the proposed change, and never on anyone else's accounts. With more than one account, name it ("on my Visa"); it asks which otherwise (new `finance.expense.add`, Household Assistant 1.5.0).

## 1.4.3

- **The Household Assistant's quick answers know Finance's words for recurring charges and balances** ("standing orders", "direct debits", "bank balance", "credit card balance", …), so those questions are answered in a second without the AI (Household Assistant 1.4.0).

## 1.4.2

- **Transfers link when your data changes, and fast**: matching now runs whenever a statement or CSV is imported or a transaction is added, reviewed, included, deleted or restored — opening the Dashboard or Transfers no longer changes anything — and it takes moments on a long history instead of minutes. Amounts a cent apart now always match.
- **Saving a statement or CSV no longer holds up the app** while the AI suggests categories: the rows are saved in one short step around the AI's answer.
- **A statement, utility bill or toll statement can't be deleted while it is being read** (wait until it is done or failed); one deleted anyway in that moment saves nothing. Wiping someone's data waits the same way.
- **Restore brings back only what was deleted with it**: a transaction deleted on its own before its statement stays deleted, and nothing is restored onto a deleted account (restore the account first). The Dashboard leaves out rows on a deleted account.
- **Purging** a statement or CSV import takes all its deleted transactions, an account takes its statements' PDFs, and files are removed only once the rows are gone.
- **Credit cards read the wrong way round** are put right before looking for transactions already on file, so a charge already imported from a CSV is recognised.
- **Amounts are stored in whole cents** (older ones are rounded once), and a CSV or typed amount like *12,50* is refused instead of being read as 1250.
- **The AI's answers are checked**: a date is stored as YYYY-MM-DD, a row with an unreadable date or amount (or NaN/infinity) is left out and listed on the debug page, and long text is cut to the field's size.
- **New setting — Most pages per PDF** (Admin → App settings → AI, default 30): a longer PDF is refused with a message instead of sending every page to the AI. Raise it if your statements are longer.
- **After a restart**, uploads that were only waiting their turn are read; only the one being read becomes an error with Restart. A job keeps the AI settings it started with.
- **Links from Home Assistant to a month** open that month on the Dashboard (they opened the current month).
- **Uploading while viewing as someone** puts the statement in their data, not yours.
- **Transactions** shows 100 rows a page (250 or 1000 if you choose); the count and total cover every matching row, and the CSV export is streamed. Saving a row's category keeps your search.
- **Also apply to all existing transactions** (Categories) changes the transactions the new rule matches, decided by all your rules as before; other transactions keep their category. A blank rule pattern is refused.
- **Forms that can't be saved** show a page saying why, with a link back, instead of raw text; an account with no name or an unknown type is refused instead of failing.
- **Faster pages**: settings are read from memory, toll cars and devices are added up once, the Recurring page is quicker and kept until the data changes, the utility dashboard polls with a tiny request, PDF page images are cached by the browser, and new database indexes.
- **Database upgrades are all-or-nothing**, and a copy of the database is kept as *finance.db.pre-NNNN* before one runs. Home Assistant backups now get a complete database file (its recent changes are written in first).
- **Restoring a backup** waits for a PDF being read to finish (or asks you to try again), and a failed download no longer leaves a temporary copy behind.
- Smaller fixes: *Processing…* and *isn’t* show as characters, a missing cost no longer breaks Compare, the bulk-delete question says an admin can restore for 30 days, *Not found* no longer always says "account", and the Supervisor's watchdog now checks the app answers (not just that its port is open).
- **Security**: uploads and forms are size-limited before they are read (20 files per upload, 2 MB per form), pages only show messages the app wrote (an address can't put its own text on a page), a "next" address can't leave the app, PDF viewer searches are limited, long statement text can no longer make the parser spin for minutes, a no-time-limit query stops after 30 minutes, and an AI debug re-send after 30.
- **Shared code**: an optional **App-messages signing key** (Admin → App settings → Household Assistant) — put the same key in every household app so they can tell each other's messages from a fake; API answers are never cached, and the app's scripts and styles are cached by the browser until the next version, so pages load faster.

## 1.4.1

- **Re-extracting a utility bill keeps the confirmed ones**: re-extracting the pending gas bill from a dual-fuel PDF read the whole upload again and replaced its electric bill too — even after you had confirmed it and corrected its meter figures. It's now refused while another bill from the same PDF is confirmed (correct the bill on its Check & confirm page instead), and the notes you type are kept on the bill you were reviewing.
- **Amounts read the way you write them**: a CSV amount like *$1,234.56* or *(12.50)* (a negative) is imported instead of failing the row with a confusing message, and *nan* or *inf* in an amount, cost, usage or meter figure is refused with a clear message — a *nan* in one CSV row used to stop the whole import.
- **Corrected dates are stored the usual way**: a date edited on Review or a utility bill's Check & confirm page (e.g. *08/03/2026*) is saved as 2026-08-03 like every other date, and one the app can't read is refused instead of saved as typed.
- **Insert anyway** on a skipped duplicate does nothing once its statement or CSV import has been deleted.
- **Household Assistant**: it no longer answers about someone's shared data after they turn off *Let the Household Assistant answer for me*.
- After saving a category or a bulk change, the app only returns to one of its own pages.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; the AI connection no longer follows redirects; pages no longer use inline event handlers, and backup restores say clearly when a file is too large.

## 1.4.0

- **Utility bills wait for your confirmation**, like statements: each bill shows *Awaiting confirmation* with **Check & confirm** (correct anything, then Confirm) and **Re-extract** (tell the AI what was wrong and read the PDF again). Tick **Skip the confirmation step** when uploading to save them straight away.
- **Solar meter figures read right**: the electric meter table is now also read from the bill's own text, so a figure the AI misread — e.g. *Off Net Generated by Customer* 76 kWh read as 0 — is taken as printed, and the review page lists what was corrected. The AI is also told more plainly that solar homes often have net generated kWh.

## 1.3.1

- **Viewing as someone else**: the dashboard's filters (dates, accounts, categories) kept you on that person's view — they used to drop back to your own.
- **Restoring a backup asks first**: Admin → Storage → Import now says it replaces all data for every user and waits for you to confirm.
- A backup that failed the check before restoring no longer leaves its temporary copy behind.

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
