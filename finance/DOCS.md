# Finance Dashboard

A private finance app for your household, inside Home Assistant. Upload your bank and
credit-card statements (PDF or CSV); an AI model reads them, you confirm, and the app keeps
your transactions, categories, transfers, recurring charges and reports. Nothing is shared
outside Home Assistant unless you choose a cloud AI provider (see **AI and privacy**).

## Getting started

1. In Home Assistant: **Settings → Apps → Install app → ⋮ (top right) → Repositories**, paste
   `https://github.com/sameerkotra/ha-apps`, **Add**. Then, in **Settings → Apps → Install
   app**, find **Finance Dashboard**, **Install**, then **Start** on its **Information** tab
   (the first install builds for a few minutes). It opens from the sidebar (**Finance**;
   **Show in sidebar** is on the Information tab); there is no port to open. Finance Dashboard
   runs on 64-bit systems only (see **Limits and good to know**).
2. The first page says **No admin yet**. Open the app's **Configuration** tab, add your
   Home Assistant user name to **admin_users** (the page shows it), save, and restart the
   app (Information tab → **Restart**). Several people can be admins.
3. In the app, go to **Admin → App settings** and set up the AI (below). **Test connection**
   checks it without costing anything.
4. **Setup → Accounts**: add your checking, savings and credit-card accounts.
5. **Upload → Statements**: pick the account and upload its statement PDFs.

Everyone in your Home Assistant can open the app. Each person sees only their own data; the
admin can give someone access to another person's data (**Admin → Users**).

## AI and privacy

The app needs an AI model that can read images, set on **Admin → App settings → AI**:

- **Ollama** (on your own network) — nothing leaves your home. Enter its address (e.g.
  `http://192.168.1.10:11434`) and a vision model (e.g. `qwen2.5vl:7b`). A large local model
  can take several minutes per statement.
- **OpenAI-compatible** — OpenAI, OpenRouter, Groq, LM Studio, vLLM and similar. Address blank
  means OpenAI; add the access key from your account.
- **Anthropic Claude** — add the access key from the Anthropic Console.

Test connection lists the models the provider offers. An optional **text model** (a cheaper
one) is used for suggesting categories and writing SQL. The access key is stored in the app's
database, is never shown again, and is left out of database downloads.

**Most pages per PDF** (default 30) — every page of a PDF goes to the model as an image in one
request, so a longer PDF is refused with a message saying so instead of taking a very long time
(or costing a lot). Raise it if your statements are longer. A PDF upload reads with the settings
it started with: changing the AI settings meanwhile applies from the next upload.

**Privacy.** With a provider outside your home network, the pages of every PDF you upload are
sent to it as images, and transaction descriptions are sent to suggest categories. The app then
shows a notice on Home, App settings and the upload pages. That provider's own privacy and
data-retention rules apply.

**Cost.** Every AI request is recorded with its tokens and time on the admin debug pages, and
added to a running total on **Admin → AI usage**. Enter your provider's prices per million
tokens on App settings to see the cost.

## What the app does

### Home
Income and spending by category for this week, this month, last month, this or last year, all
time or any month; drill into the transactions behind a figure. **Compare** puts two months
or two years side by side. When one of your statements is overdue (see **Expected statements**
below), an amber card above the charts lists it with **Upload** and **Dismiss**; a card payment due
soon or overdue (see **Card payments** below) gets one too, with **Mark as paid**.
- **Subscription changes**: a recurring charge whose price changed ("Netflix 15.49 → 17.99 (+16%)
  since Sep 12") or a new subscription (one that became recurring in the last 45 days), each with
  **Dismiss** (see **Recurring** below).

### Transactions
- **All**: search by notes, description, account, category, money in/out, excluded, dates and
  amounts; edit categories (and save a rule), notes and Exclude; select several at once;
  export to CSV. A long result shows 100 rows a page (250 or 1000 if you choose); the count and
  total are for every matching row, and the export has them all.
- **Transfers**: money between your own accounts (card payments, checking ↔ savings) is linked
  automatically — when a statement or CSV is imported and whenever a transaction is added,
  reviewed, included, deleted or restored — and counted as neither income nor spending; confirm
  the ambiguous ones. **Link unambiguous** runs the matching on demand.
- **Recurring**: subscriptions and bills detected from your history, weekly to yearly. On top,
  **Subscription changes**: a price change is the latest charge differing from the one before it,
  after at least two charges at the old price; a bill whose amount changes every time (electricity,
  water) only counts when the latest is outside its usual range. A change is shown while its charge is
  from the last 90 days. **Dismiss** keeps that change away for good; a later, different change of the
  same charge shows again. Only the owner of the account sees these, not people it's shared with.
- **Accounts**: each account's latest transaction, so you know which statement is due, and its
  **Next statement** ("Oct 12", "Overdue 3 days" or "Dismissed") when it has an expected date, and a
  card's **Payment due** ("Nov 5 (in 3 days)", "Due today", "Was due 2 days ago", "Paid · due Nov 5")
  when it has a payment due day.

### Upload
- **Statements** is one page for every kind of bank file. From the top:
  - **Overdue statements** (when any are), with **Upload** and **Dismiss**. A reminder tapped on
    your phone opens this page with its account picked. The **Upload** menu item shows a small
    count while anything is overdue.
  - **Accounts** (folded until you open it; the line shows how many, and how many are overdue or
    missing a month; it stays open next time once you open it): a small card for each account saying how far its uploads go ("up to Sep
    2026"), a warning when a month is missing ("Aug missing" — counted by its statement dates when
    the account has an expected statement date, otherwise by calendar months), whether its
    statement is overdue, and its preferred upload method. Its **Upload** button picks that account
    and opens the file picker on that kind of file. On a phone the cards scroll sideways.
  - **Upload files**: drop PDF statements, QFX / OFX / QBO downloads and CSV exports on the box
    together (on a phone: **Choose files**, which opens the Files app). Each file is listed before
    anything is sent, with its type, size and the account it will go to, which you can change (or
    **+ Create new account…**), and ✕ to take it off the list:
    - a **QFX** file goes to the account with the same last 4 digits (**Match by account number**);
    - a **PDF** goes to the account whose last 4 digits are in the file name, otherwise the one you
      used last time;
    - a **CSV** uses its own Account column when it has one, otherwise the account you pick.
    The account picked above the box is used for every file you add after it. A file that isn't its
    account's preferred type gets a small "not this account's usual type" mark — a reminder, never a
    block. Options appear only for the kinds of file listed: **Skip the PDF confirmation step** for
    PDFs, **Header and data rows** for CSVs (see below). **Upload** sends them all; each file's row
    then shows how it went (queued, reading, done with its count, needs review, or the error), and a
    question about a file (which account a QFX file is for, which date column or header row a CSV
    has) is asked in its own row. At the end one line sums it up ("3 files · 2 statements, 1 CSV ·
    412 transactions · 9 duplicates skipped · 1 needs review") with **Review now**; the page isn't
    reloaded. Without JavaScript the box is a plain form for PDF and QFX files.
  - **Add by hand**, the second tab: one transaction at a time.
  - **Waiting for you**: statements to confirm against the PDF, to review, or that failed (with
    **Restart**), kept above the history.
  - **Upload history**: statements and CSV imports in one list, newest first, with type, account,
    period, count and status, sorting and paging; the chips above it show only one type, account
    or status. **Delete** removes an upload with its transactions (admins also see **debug**).
- **PDF statements** (up to 50 MB each, at most 20 files at a time, with a text layer — scanned
  images aren't supported) are read by the AI and checked against the balances and amounts printed
  in them. Confirm each one against the PDF, or **Re-extract** telling the AI what was wrong; the
  PDF is deleted once it's complete. Some banks' PDFs are always read right: tick **Skip the PDF
  confirmation step** and **Remember this for …** (or set it on the account's page under
  **Uploads**) and that account's PDFs skip it from then on.
- **QFX / OFX files**: most banks offer "Download transactions → Quicken (QFX)" or "Money (OFX)"
  (`.qfx`, `.ofx` or `.qbo`, up to 20 MB each). A QFX file is read in a second, with no AI and no
  confirmation step: it already holds the exact rows, the account number and the closing balance.
  With **Match by account number** each account in the file goes to yours with the same last 4
  digits (a file with checking and savings together becomes two statements); if none matches,
  you're offered to create it (named after the bank and the last 4, set to "QFX only"). If you pick
  an account whose last 4 differ from the file's, you're asked first. Downloads that overlap ("the
  last 90 days" every month) are fine: rows already in, from an earlier QFX file or from a PDF or
  CSV, are skipped and listed for **Insert anyway**. The statement's opening balance is worked out
  from its closing balance and rows, and when the previous statement ends the day before, a
  difference flags it for review (usually a missing download in between). Credit card files are
  turned the app's way round (a purchase is money out). The same file twice is refused;
  investment and loan statements aren't supported. A QFX file isn't kept after it's read.
- **CSV files** (up to 20 MB each) need Date, Description and Amount columns — or Debit and Credit
  instead of Amount (on a credit card Debit is a purchase and Credit a payment; on checking and
  savings Debit is money out). Category and Account are optional. A header that isn't the first row
  (a bank's lines about the name, card and report period above it) is found by itself; to choose,
  open **Header and data rows**, tap **Show the file's first rows** and tap the header row (or type
  the header row and the row the data starts on). After a CSV imports you can **Remember this
  layout for** the account: its header row, data row and date column are used by themselves next
  time ("Using Visa's saved layout · change"); clear it on the account's page under **Uploads**.
- **Review**: everything waiting for you. Under **Skipped as possible duplicates**, tick rows (or
  **Select all**) and choose **Insert selected** or **Keep selected skipped** to handle many at once.
- **Utility bill** and **Toll statement**: when those parts are switched on (below). Each utility
  bill waits for you like a statement: **Check & confirm** opens its figures (correct anything, then
  Confirm), **Re-extract** reads the PDF again with your notes. Tick **Skip the confirmation step**
  when uploading to save bills straight away. On an electric bill the meter table (delivered by Xcel,
  delivered by you, net delivered, net generated) is also read from the bill's own text, and any
  figure the AI read differently is listed when you check it.

### Bills (optional)
- **Utilities**: electric, gas and water bills with usage and cost per period, and Compare.
  Xcel Energy and Aurora Water are read with tuned rules; any other provider works too — its
  bills always wait for you to check and confirm, even when you skip the confirmation step.
- **Tolls**: toll road statements by car and tag, trips and patterns.

Both are off on a new install: switch them on under **Admin → App settings → Features**.
Leave them off if you don't need them — the rest of the app works the same. Tolls reads toll road
statements that list each pass under a tag (device) and plate heading — date, time, location, lane and
amount — with a grand total; a statement laid out differently may not be read.
Turning a part off (untick it in the same place) hides it everywhere — its menu entries, upload page
and Bills pages — but deletes nothing: switch it back on and everything is still there. An
install that already had utility bills or toll statements before keeps those parts switched on.

### Reports
Saved reports anyone can run on their own data, with filters (account, period, category),
totals, charts and CSV. The admin writes them on **Admin → Query** in SQL — or asks the AI to
write the SQL from a plain-English question.

### Setup
- **Accounts**: add, rename, archive, set the preferred upload type (any, PDF, CSV or QFX — a
  reminder on the Upload page, never a block), a website link, the **Expected statement** and, for a
  credit card, **Payment due**.
- **Categories**: your own categories and rules (exact, prefix or contains); rules are tried
  before the built-in ones and the AI. **Also apply to all existing transactions** gives the
  transactions the new rule matches the category your rules give them (a more specific rule
  still wins); other transactions keep theirs.
- **Settings**: your own settings (not App settings): **My reminders**, **Card payments** and
  **Subscription changes**.

#### Expected statements and reminders
- **Expected statement** (on Add account and each account's Edit details): Off, Monthly, every 2, 3
  or 6 months, or Yearly; **On day** (1–31, or the last day of the month — the 29th, 30th and 31st
  fall on a shorter month's last day); **Starting**, the month the series counts from (when it isn't
  monthly); and **Remind me** for this account. You pick the day the bank has the statement ready:
  the app never guesses it. The account's page shows when the last statement arrived and when the
  next is expected.
- **Received**: any statement (PDF or QFX, even one still being read, but not one that failed) or
  CSV import with at least one row for the account, uploaded on or after the expected date.
  Transactions added by hand don't count.
- **Overdue**: from the day after the expected date, until it's uploaded, you **Dismiss** it (on
  Home or Upload: that statement gets no more reminders, the next one starts fresh), or the next
  expected date arrives — only the latest one is ever shown, so a missed month never piles up. A
  new or changed expected date never makes an earlier statement overdue; neither does restoring
  or unarchiving an account (archived and deleted accounts are never overdue).
- **My reminders** (Setup → Settings): nobody is reminded until they choose. **No notifications**,
  or **Remind me** on 1 to 6 days after the expected date (On the day, 1, 2, 3 or 5 days, 1 week,
  10 days, 2, 3 or 4 weeks), **Then keep reminding** every week or every 2 weeks (never past the
  next expected date) and **At** a time of day (Home Assistant's time zone). Several statements due
  together come as one notification. A reminder goes to every phone Home Assistant links to you
  (Settings → People → your person → Track device) plus any extra notify service an admin adds for
  you on Admin → Users; if none is found the card says so. It names the account only, never its
  number, and tapping it opens Upload with that account picked. If the app was off when reminders
  were due, only the newest is sent; one Home Assistant couldn't deliver is tried again for six
  hours. The notices on Home and Upload show whatever you choose here.
- Only the account's owner is reminded and sees its notices — not people it's shared with. An admin
  acting as the owner sees the owner's and can change their My reminders.

#### Card payments
- **Payment due** (credit cards only, on Add account and the card's Edit details): Off, or monthly on
  a day (1–31) or the last day of the month — the 29th, 30th and 31st fall on a shorter month's last
  day. You set it; the app never reads it from statements. A new or changed day never makes an
  earlier due date count, and neither does restoring or unarchiving the card.
- **Paid**: a payment to the card recorded on or after the previous due date — any money-in row on
  the card, a linked transfer from checking included. One payment counts for one due date: the
  latest one up to the due date, else (paid late) the first one after it. Or tap **Mark as paid**.
- **Shown** on Home and Upload from 7 days before the due date, and after it until the next due date
  arrives, unless paid; the accounts lists have a **Payment due** column and the card's page a
  **Payment due** line (with **Mark as paid**).
- **Card payments** (Setup → Settings): nobody is reminded until they choose. **No notifications**, or
  **Remind me** on 1 to 6 of: 7, 5, 3, 2 or 1 days before, on the day, 1 or 3 days after, **At** a
  time of day. Several cards due together come as one notification; it names the card only, never its
  number, and tapping it opens the card's page (several: Home). Only the newest is sent after the app
  was off; a failed one is tried again for six hours.

#### Subscription change notifications
- **Subscription changes** (Setup → Settings): **No notifications** (until you choose) or **Notify me**
  at a time of day: each price change or new subscription is sent once, at that time on the day it is
  found (or the next day, when it's found later in the day); several come as one notification, and
  tapping it opens Recurring. A dismissed change isn't sent.

### Look and feel
- **Theme** (bottom of the menu; **More** on a phone): Midnight (the default), Slate, Daylight,
  or Auto (Daylight when your device is set to light, Midnight when it is dark). It is
  remembered by your browser; if you had picked Sandstone before, you now get Daylight.

### Admin (admins only)
- **Storage**: stored PDFs, and database download / import for backups.
- **Query**: read-only SQL on the data of the person you're acting as, and report editing.
- **Users**: give a person access to another person's data (e.g. one shared household view),
  add extra **Notify** services for a person's reminders and notifications (a speaker, a second phone:
  `notify.<name>`), and export, import or delete one person's data (below).
- **Recently deleted**: anything deleted in the last 30 days can be restored; after that it is
  erased.
- **AI usage**: the running total of every AI request since the app started counting: requests,
  input, output and total tokens, time and cost, all time and by month, by kind of request
  (reading PDFs, categories, Write SQL) and by model. Re-processing or deleting a statement
  doesn't lower it. An install that already had statements starts the total from the AI usage
  stored on each of them (their last processing run only).
- **App settings**: AI, AI usage display and prices, Features, **Statement reminders** (on by
  default; off hides the phone reminders, the notices and the Upload count for everyone, but keeps
  every account's expected date and everyone's choices), **Card payment reminders** and
  **Subscription change alerts** (both on by default; off hides their notices, column and cards and
  stops their notifications, keeping every card's due day, the dismissed changes and everyone's
  choices) and Household Assistant (with the app-messages signing key). Changes apply at once.

Admins can **act as** any user (the switch at the top right) to see and fix their data.

### Admin → Users → Data (one person's data)
Each person's card on **Admin → Users** has a **⋯ Data** button, and **Import a person's data…**
sits above the list.

- **Export their data** downloads one `.zip` with everything that person has: accounts,
  statements, transactions, CSV imports, rules and categories, recurring choices, reminder and
  notification choices (with the statements, card payments and subscription changes they dismissed),
  utility bills and toll statements with their cars, tags and choices. You can leave out the statement PDFs
  still kept, include Recently deleted items, and leave out the text read from statements. The
  file isn't encrypted: it holds their bank data, so keep it somewhere safe. Shared access, saved
  reports, App settings and the AI usage total are never in it. With PDFs it is prepared in the
  background and offered for an hour.
- **Import** loads such a file (made here or on another install) into anyone in the list. First
  it shows what's in it, then you choose **Add to what they have** (what they already have isn't
  added twice: the same account name and last 4 digits, the same PDF, the same transaction —
  account, date, amount and description — the same rule, category, car, tag or toll pass) or
  **Replace what they have** (their current data is deleted first; type their name to confirm).
  Every row is checked like the app's own forms; a file that was changed, comes from another app
  or from a newer version of this one is refused. Transfers are paired and toll trips worked out
  again afterwards. Give shared access again yourself if it's needed.
- **Delete their data…** deletes everything above for good, at once, plus shared access to and
  from them. **Keep them** leaves them in the list, empty; **Forget them** removes them too (they
  come back as someone new if they open the app again). Type their name to confirm. Unlike
  **Wipe all data** (Recently deleted), nothing can be restored. Backups made before, and the copy
  kept before a database upgrade, still hold their data.

Only one import or delete runs at a time, and neither runs while one of that person's uploads is
being read.

### The Household Assistant

If the household also uses the **Household Assistant** app, an admin can let it ask Finance
(**Admin → App settings → Household Assistant → Answer the Household Assistant**; **off** until
turned on, because money is private). Then each person can ask about **their own** money — and
about the data an admin shared with them on **Users**, by that person's name — never anyone
else's. An admin's "act as" doesn't apply to the assistant. Anyone can turn off **Let the Household Assistant
answer for me** on **Who am I**; the assistant then won't answer their questions from Finance.

- "How did we do this month?" — a month's income, spending, net and top categories, counted
  exactly as the Overview counts them (transfers and excluded rows left out);
- "How much did we spend on groceries in September?" — spending by category, or one category's
  10 largest charges: the bank's description, date, amount and account — **never your notes**;
- "What subscriptions do we pay for?", "How much is Netflix?" or "What's charged to my Visa every
  month?" — the recurring charges, with their next expected date, what each costs a year, its last
  price change, and the total a month and a year;
- "What bills are coming up?" — the recurring charges expected soon, and the card payments due
  that aren't paid yet;
- "How much did we spend at Costco this year?" — the total at one shop or company over the last
  months (12 unless you say), each month's, and the latest charges;
- "What's the balance on my accounts?", "What's our net worth?" — each account's balance as its
  latest statement printed it (for a card, what was owed), with that statement's month;
- "Which statements are overdue?", "When is my Visa statement due?", "Am I missing any
  statements?" — each account's expected statement date, its last upload, the next expected date,
  whether it's overdue, due today, received or dismissed, and the months with nothing uploaded (the
  same as the Upload page shows);
- "When is my credit card payment due?", "Did I pay the Visa?" — each card's due date, whether
  it's paid (and when, and how much), marked as paid or not, the last payment, and what the latest
  statement said was owed. A payment's description is never given;
- "Did any subscription go up?", "What new subscriptions started?" — price changes, bills outside
  their usual range and new subscriptions, the ones you dismissed on Home too (marked so);
- "What finance dates are coming up?", "Any due dates this week?" — statements expected, card
  payments due and bills, in one list, anything overdue first;
- "Give me a finance summary", "How are my finances?" — what's due in the next two weeks, this month so far, and any subscription change from the last month, each part
  short. "How did we do this month?" still gives the month's figures.

Only live, unarchived accounts are used, and an account number never shows beyond its last 4
digits. Common questions like these are answered without the AI.

**App-messages signing key** (same section, optional): the household apps talk over Home
Assistant's event bus, and without a key any app on Home Assistant's network could send a message
as any other. Paste the same long random key into every household app's App settings (at about
the same time: an app with a key refuses messages from apps without it). It is never shown again
and is left out of database downloads.

The answers travel through Home Assistant's event bus, which Home Assistant's recorder keeps in
its history unless told not to. **If you turn this on**, add this to Home Assistant's
`configuration.yaml` and restart Home Assistant:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

## Configuration (the app's Configuration tab)

- **admin_users** — Home Assistant user names (or user ids) of the app's admins. Read when the
  app starts: restart after changing it. The "How the app sees you" page (your name at the
  bottom of the menu) shows what to enter.
- **trusted_client_ips** — leave empty. Only if the app answers 403 naming the address Home
  Assistant's proxy used, add that address.

Everything else is set inside the app on **Admin → App settings**.

## Limits and good to know

- **64-bit only.** Finance Dashboard runs on amd64 and aarch64 (e.g. a Raspberry Pi 4 or 5 with
  the 64-bit Home Assistant OS). It isn't offered on 32-bit systems (armv7, armhf, i386).
- One PDF is read at a time; others wait in a queue (shown on the upload page). If the app
  restarts, the ones still waiting are read after the restart; the one being read shows an error
  with **Restart**.
- A statement, bill or toll statement can't be deleted while it is being read: wait until it is
  done (or failed).
- PDFs need a text layer (no OCR). Password-protected PDFs aren't supported.
- A PDF is kept until its statement is confirmed, then deleted.
- The app is reached only through Home Assistant (ingress); it has no port of its own.
- Back up with **Admin → Storage → Download database**; Home Assistant backups of the app
  include its data too. Importing a database, even one from an older version of the app, brings
  it up to date straight away (the AI usage total included); restart the app afterwards.
  Backups leave out access keys and passwords; after restoring on a new install, enter them again. (Importing keeps the AI access key this install already has.)
  An import waits up to a minute for a PDF being read to finish, and otherwise asks you to try
  again. Home Assistant backs the app up while it runs: just before, the app writes all recent
  changes into its database file, so the backup is complete. Before the app upgrades its database
  it keeps a copy of it next to it (`finance.db.pre-NNNN`, the latest only).
- A no-time-limit query on **Admin → Query** still stops after 30 minutes, and the admin's debug
  "re-send to the AI" waits at most 30 minutes.
