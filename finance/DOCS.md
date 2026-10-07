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
  export to CSV.
- **Transfers**: money between your own accounts (card payments, checking ↔ savings) is linked
  automatically and counted as neither income nor spending; confirm the ambiguous ones.
- **Recurring**: subscriptions and bills detected from your history, weekly to yearly.
- **Accounts**: each account's latest transaction, so you know which statement is due.

### Upload
- **Statements**: PDF statements (up to 50 MB, with a text layer — scanned images aren't
  supported) and CSV exports. Each PDF is read by the AI and checked against the balances and
  amounts printed in it. Confirm it against the PDF, or **Re-extract** telling the AI what was
  wrong. Possible duplicates are skipped and can be inserted anyway.
- **Review**: everything waiting for you.
- **Utility bill** and **Toll statement**: when those parts are switched on (below).

### Bills (optional)
- **Utilities**: electric, gas and water bills with usage and cost per period, and Compare.
  Xcel Energy and Aurora Water are read with tuned rules; any other provider works too — its
  bills always wait for you to check and confirm.
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
  before the built-in ones and the AI.

### Look and feel
- **Theme** (bottom of the menu; **More** on a phone): Midnight (the default), Slate, Daylight,
  or Auto (Daylight when your device is set to light, Midnight when it is dark). It is
  remembered by your browser; if you had picked Sandstone before, you now get Daylight.

### Admin (admins only)
- **Storage**: stored PDFs, and database download / import for backups.
- **Query**: read-only SQL on the data of the person you're acting as, and report editing.
- **Users**: give a person access to another person's data (e.g. one shared household view).
- **Recently deleted**: anything deleted in the last 30 days can be restored; after that it is
  erased.
- **AI usage**: the running total of every AI request since the app started counting: requests,
  input, output and total tokens, time and cost, all time and by month, by kind of request
  (reading PDFs, categories, Write SQL) and by model. Re-processing or deleting a statement
  doesn't lower it. An install that already had statements starts the total from the AI usage
  stored on each of them (their last processing run only).
- **App settings**: AI, AI usage display and prices, Features, and Household Assistant. Changes apply at once.

Admins can **act as** any user (the switch at the top right) to see and fix their data.

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
  with their next expected date.

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
- One PDF is read at a time; others wait in a queue (shown on the upload page).
- PDFs need a text layer (no OCR). Password-protected PDFs aren't supported.
- A PDF is kept until its statement is confirmed, then deleted.
- The app is reached only through Home Assistant (ingress); it has no port of its own.
- Back up with **Admin → Storage → Download database**; Home Assistant backups of the app
  include its data too. Importing a database, even one from an older version of the app, brings
  it up to date straight away (the AI usage total included); restart the app afterwards.
  Backups leave out access keys and passwords; after restoring on a new install, enter them again. (Importing keeps the AI access key this install already has.)
