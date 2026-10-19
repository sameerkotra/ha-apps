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
or two years side by side.

### Transactions
- **All**: search by notes, description, account, category, money in/out, excluded, dates and
  amounts; edit categories (and save a rule), notes and Exclude; select several at once;
  export to CSV. A long result shows 100 rows a page (250 or 1000 if you choose); the count and
  total are for every matching row, and the export has them all.
- **Transfers**: money between your own accounts (card payments, checking ↔ savings) is linked
  automatically — when a statement or CSV is imported and whenever a transaction is added,
  reviewed, included, deleted or restored — and counted as neither income nor spending; confirm
  the ambiguous ones. **Link unambiguous** runs the matching on demand.
- **Recurring**: subscriptions and bills detected from your history, weekly to yearly.
- **Accounts**: each account's latest transaction, so you know which statement is due.

### Upload
- **Statements**: PDF statements (up to 50 MB each, at most 20 files at a time, with a text layer —
  scanned images aren't supported) and CSV exports (up to 20 MB each). A CSV whose header isn't
  the first row (a bank's lines about the name, card and report period above it) is found by
  itself; to choose, open **Header and data rows** on the CSV form, tap **Show the file's first
  rows** and tap the header row (or type the header row and the row the data starts on). Each PDF is read by the AI and checked against the balances and
  amounts printed in it. Confirm it against the PDF, or **Re-extract** telling the AI what was
  wrong. Possible duplicates are skipped and can be inserted anyway.
- **Review**: everything waiting for you.
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
- **Accounts**: add, rename, archive, set the preferred upload type and a website link.
- **Categories**: your own categories and rules (exact, prefix or contains); rules are tried
  before the built-in ones and the AI. **Also apply to all existing transactions** gives the
  transactions the new rule matches the category your rules give them (a more specific rule
  still wins); other transactions keep theirs.

### Look and feel
- **Theme** (bottom of the menu; **More** on a phone): Midnight (the default), Slate, Daylight,
  or Auto (Daylight when your device is set to light, Midnight when it is dark). It is
  remembered by your browser; if you had picked Sandstone before, you now get Daylight.

### Admin (admins only)
- **Storage**: stored PDFs, and database download / import for backups.
- **Query**: read-only SQL on the data of the person you're acting as, and report editing.
- **Users**: give a person access to another person's data (e.g. one shared household view), and
  export, import or delete one person's data (below).
- **Recently deleted**: anything deleted in the last 30 days can be restored; after that it is
  erased.
- **AI usage**: the running total of every AI request since the app started counting: requests,
  input, output and total tokens, time and cost, all time and by month, by kind of request
  (reading PDFs, categories, Write SQL) and by model. Re-processing or deleting a statement
  doesn't lower it. An install that already had statements starts the total from the AI usage
  stored on each of them (their last processing run only).
- **App settings**: AI, AI usage display and prices, Features, and Household Assistant (with the
  app-messages signing key). Changes apply at once.

Admins can **act as** any user (the switch at the top right) to see and fix their data.

### Admin → Users → Data (one person's data)
Each person's card on **Admin → Users** has a **⋯ Data** button, and **Import a person's data…**
sits above the list.

- **Export their data** downloads one `.zip` with everything that person has: accounts,
  statements, transactions, CSV imports, rules and categories, recurring choices, utility bills
  and toll statements with their cars, tags and choices. You can leave out the statement PDFs
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
- "What subscriptions do we pay for?" and "What bills are coming up?" — the recurring charges,
  with their next expected date;
- "How much did we spend at Costco this year?" — the total at one shop or company over the last
  months (12 unless you say), each month's, and the latest charges;
- "What's the balance on my accounts?" — each account's balance as its latest statement printed it
  (for a card, what was owed), with that statement's month.

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
