# Finance Dashboard — QFX / OFX statements, and a new Upload page

Status: **spec only, nothing built.** Covers two things: uploading a bank's **QFX** (Quicken) or **OFX** download
as a statement (§1–§9), and ideas for redesigning **Upload → Statements** (§10). Decisions still open are in §11.

---

## 1. What it is

Most banks offer "Download transactions → Quicken (QFX)" or "Money / OFX". Unlike a PDF, the file already holds
exact, machine-readable rows: no AI reading, no page images, nothing to confirm against a picture. Unlike a CSV,
it has a fixed format (no column matching), a unique id per transaction (FITID), the account number and the
closing balance.

A QFX/OFX upload becomes a **statement** like a PDF does (shows in Statement history, has a period and a balance,
can be deleted as a unit, its rows are checked for duplicates and categorized), but it's read in a second, with
no queue and no AI.

## 2. Where it goes

- **Upload → Statements**, the same form as PDFs: the file picker accepts `.pdf`, `.qfx`, `.ofx` (and `.qbo`,
  QuickBooks' variant of the same format). A mix in one upload is fine; each file goes its own way by its type
  (checked by its content, not just its name — §4.1).
- The "Skip the PDF confirmation step" box doesn't apply to QFX (§6). The page says so next to it when only QFX
  files are chosen.
- **Preferred upload method** gains **QFX** (§2a).

## 2a. Preferred upload method: QFX

Today each account's **Preferred upload method** is *Either PDF or CSV* / *PDF only* / *CSV only* (`both`, `pdf`,
`csv`); it's a nudge, never a block. It gains QFX:

| Stored | Shown in the menu | Shown in lists |
|---|---|---|
| `both` | Any (PDF, CSV or QFX) | Any |
| `pdf` | PDF only | PDF only |
| `csv` | CSV only | CSV only |
| `qfx` | QFX / OFX only | QFX only |

- **Where it's set:** Setup → Accounts (the add form and each account's edit page, `accounts_list.html`,
  `account_detail.html`), and the new-account fields on the Upload form (§5's "create it"). An account created
  from a QFX file starts as **QFX only**.
- **Where it's shown:** the Accounts list and Transactions → Accounts tab (`transactions_accounts.html`), as
  today.
- **The nudge on upload:**
  - For PDFs (`statement-upload.js`) and CSVs (`csv-import.js`), it now also says QFX ("This account's preferred
    upload method is QFX, but you're uploading a PDF. Wrong account, or upload anyway?").
  - A QFX file sent to a `pdf` or `csv` account gets the same nudge.
  - `both` never nudges.
  - With several files of different types, it's asked once, naming each type that doesn't match.
- **Server:** `_sanitize_upload_method` (`routes/accounts.py`) accepts `qfx`, and anything unknown still becomes
  `both`. The database CHECK gains `'qfx'` (migration 0028, §7).
- **With the redesign (§10):** the accounts strip shows the preferred method on each card, and its **Upload**
  shortcut opens the file picker filtered to that type (`accept=".qfx,.ofx,.qbo"` for QFX, while still allowing
  any). In the drop zone, a file whose type doesn't match its account's preference gets a small "not this
  account's usual type" mark rather than a dialog.
- **Person-data export/import:** the value travels with `accounts`. A file from before this change can only hold
  `both`, `pdf` or `csv`, so nothing needs upgrading.
- **Tests:**
  - Each of the four values is saved and shown.
  - An unknown value becomes `both`.
  - The nudge appears for a PDF sent to a QFX account and for a QFX file sent to a PDF account; never for `both`.
  - A QFX-created account starts as `qfx`.
  - Migration 0028 keeps existing values.

## 3. The file

OFX comes in two shapes, both must work:

| | OFX 1.x (most QFX downloads) | OFX 2.x |
|---|---|---|
| Header | `OFXHEADER:100`, `DATA:OFXSGML`, `CHARSET:1252` … lines | `<?xml …?><?OFX OFXHEADER="200" …?>` |
| Tags | SGML: leaf tags often **not closed** (`<TRNAMT>-4.50`) | XML: every tag closed |

What's read (everything else is ignored, including Quicken's `<INTU.BID>`):

| OFX | Use |
|---|---|
| `STMTRS` (bank) / `CCSTMTRS` (credit card) | one statement per block; a file may hold several (§5) |
| `BANKACCTFROM/ACCTID`, `ACCTTYPE` · `CCACCTFROM/ACCTID` | the account: last 4 digits, and checking/savings/credit card |
| `CURDEF` | currency (shown; a non-default currency is a warning, not a refusal) |
| `BANKTRANLIST/DTSTART`, `DTEND` | the period |
| `STMTTRN`: `TRNTYPE`, `DTPOSTED`, `DTUSER`, `TRNAMT`, `FITID`, `NAME`, `MEMO`, `CHECKNUM` | one transaction each |
| `LEDGERBAL/BALAMT`, `DTASOF` | the closing balance |
| `AVAILBAL` | not used |

## 4. Reading it

1. **Recognising it:** a file counts as OFX when its first 1 KB has `OFXHEADER` or `<OFX>` (in any case). A
   `.qfx` that's really HTML (a bank's error page saved by mistake) is refused: "That isn't a QFX/OFX file — it
   looks like a web page. Download it again from your bank."
2. **No new dependency, no XML parser.** A small tolerant tag reader (`app/ofx.py`) handles both shapes: it reads
   `<TAG>value` pairs and the block structure, closing a leaf at the next `<`. It never resolves entities beyond
   `&amp; &lt; &gt; &quot; &apos; &#NN;`, so XML attacks (entities pulling in files, "billion laughs") can't
   happen. Size limit is the CSV's (20 MB); at most 20 files per upload, as for PDFs.
3. **Text encoding:** `CHARSET:1252` / `ENCODING:USASCII` → Windows-1252; `UTF-8` → UTF-8; anything else → try
   UTF-8, then Windows-1252.
4. **Dates:** `YYYYMMDD[HHMMSS[.XXX]][[±H:TZ]]` → the date part (`YYYY-MM-DD`). The time zone is ignored on
   purpose (a 23:30 −5 purchase is still that day for the person). The transaction date is **`DTUSER` when
   present, else `DTPOSTED`**, the same choice the CSV import makes ("Trans. Date" over "Post Date").
5. **Amounts:** `TRNAMT` to cents. Comma decimals (`-4,50`, some European banks) are read too.
   - **Checking / savings:** as given (negative = money out).
   - **Credit card:** the sign is flipped. OFX writes a purchase as negative, while Finance stores a purchase as
     positive (section 8). A file where most `TRNTYPE=DEBIT` rows are *positive* is a bank that already uses the
     other convention: it's read as is, and the statement says so in its debug steps.
6. **Description:** `NAME`; `MEMO` is added after " · " when it's non-empty and not already part of NAME (OFX 1.x
   cuts NAME at 32 characters, and the rest is often in MEMO). A `CHECKNUM` with no name → "Check 1234". Cut to
   the column's length like every other import.
7. **Rows that can't be used** (no date, no amount, amount not a number) are left out and listed on the debug
   page, the same as a PDF's unreadable rows. If *no* row is usable, the statement ends as `error`.

## 5. Which account

- **One block, account picked on the form:** used. If the file's last 4 digits differ from the account's (both
  known), the page asks first: "This file is for an account ending 1234, but Visa ends 9876. Use Visa anyway, or
  pick another account?", the same kind of nudge as the preferred upload method.
- **Account number on the form left as "Match by account number"** (a new first option, QFX only): each block
  goes to the person's account with the same last 4 and a matching type. No match → the page offers to **create
  it** (name from `<ORG>`/`<FI>` if present + the last 4, type from the block) or pick one.
- **Several blocks in one file** (some banks put checking and savings together): one statement per block, each
  matched as above.
- An account's last 4 is filled in from the file when it was empty.

## 6. Saving it

Done in the request, in a worker thread like the CSV import, in **one database transaction** (`BEGIN IMMEDIATE`).
No job queue and no AI reading.

- **One `statements` row per block:**
  - `source = 'qfx'` (new column; existing rows are `'pdf'`).
  - `status = 'complete'` straight away, unless rows were flagged (then `pending_review`, the same as a PDF).
  - No confirmation step, since there's no picture to check the numbers against.
  - `statement_period` is DTEND's month; the start and end dates also go in the new `period_start` and
    `period_end` columns, because QFX ranges are often arbitrary ("the last 90 days").
  - `original_filename` and `file_hash` (SHA-256) are set; `pdf_path` is empty and the file is deleted after the
    import.
- **Whole-file duplicate:** the same `file_hash` as a live statement → refused, "Already uploaded on <date>",
  as for a PDF.
- **Row duplicates.** Overlapping downloads are the normal case: "the last 90 days" every month.
  1. **FITID first:** a row with the same account and FITID as a live transaction is skipped. `transactions.fitid`
     is a new column, indexed on `(account_id, fitid)`.
  2. **Then the usual check:** a row with no FITID, or one whose FITID is new, gets the usual same-date / amount /
     description check against rows from PDFs and CSVs, which have no FITID.
  3. **Kept for review:** skipped rows go to `skipped_duplicates`, so **Insert anyway** works as it does today.
  4. **Inside one file:** two rows with the same FITID count as one; the second is listed, not inserted.
- **Balance:**
  - `new_balance` comes from LEDGERBAL. OFX has no opening balance, so `previous_balance` = closing balance −
    the sum of the block's rows.
  - The balance check (section 7) compares that with the account's previous statement's closing balance, when
    there is one for the day before DTSTART. A mismatch flags the statement for review, as for a PDF: it usually
    means a missing download in between.
  - With overlapping ranges the check uses all the period's rows, including those skipped as duplicates, since
    they belong to the period.
- **Then:**
  - categories (section 9): rules first, then the AI for the rest in one batch, after the commit, as for a CSV;
  - transfer linking (section 8);
  - recurring charges, unchanged.
- **Statement history row:** a "QFX" badge, the period as "Mar 3 – Jun 1, 2026", and the count. Delete as a unit
  works as for any statement. Re-extract and Restart aren't shown, since there's nothing to re-read.
- **Admin debug page:**
  - the blocks found and the account matched for each;
  - every row with the date chosen, the sign rule and its FITID;
  - rows left out and duplicates.
  
  Account numbers are shown as the last 4 only, never the full ACCTID.

## 7. What changes elsewhere

- **Migration 0028** (also covers §2a):
  - `statements.source` (`'pdf'` | `'qfx'`, default `'pdf'`), `statements.period_start`, `statements.period_end`;
  - `transactions.fitid` plus the index on `(account_id, fitid)`;
  - the `preferred_upload_method` CHECK gains `'qfx'` (a table rebuild, as migration 0014 did).
- **One person's data** (section 20a): the new columns travel with their tables (the export reads the columns
  from the database), and the export schema becomes 28. A file with schema 27 imports with `source='pdf'`.
- **Recently deleted / purge:** unchanged; a QFX statement has no PDF to purge.
- **Household Assistant tools:** unchanged.
- **App settings:** none. A household that never uses QFX sees no difference.

## 8. What it doesn't do

- **Direct Connect / OFX server downloads** (logging in to the bank from Finance): no. The file is downloaded by
  hand, the same as a PDF.
- **Investment statements** (`INVSTMTRS`) and loan statements: refused with "Only bank and credit card QFX files
  are supported." A file with both kinds imports the bank and card blocks and says what it skipped.
- **Pending transactions:** not part of OFX statements; nothing to do.

## 9. Checks (tests)

**Reading the file:**
- OFX 1.x SGML with unclosed tags, and OFX 2.x XML, both with Windows-1252 and UTF-8 text.
- Real-world quirks: blank lines, CRLF, lower-case tags, `&amp;` in NAME, a `<!-- comment -->`, a 32-character
  NAME continued in MEMO.

**Refused files:**
- An HTML page saved as `.qfx`.
- An empty file, and one with no `STMTTRN`.
- An investment-only file.
- A file over 20 MB.
- Entity tricks (`<!ENTITY …>`) are read as plain text and never expanded.

**Rows and signs:**
- Checking signs as given; a credit card flipped; a card file already in Finance's convention detected and read
  as is.
- DTUSER chosen over DTPOSTED; time zones ignored.

**Duplicates:**
- An overlapping second download imports only the new rows (FITID).
- A row matching a PDF row by date, amount and description is skipped.
- Skipped rows are listed for **Insert anyway**.
- The same file twice is refused.

**Accounts:**
- Matched by last 4; the different-last-4 nudge appears.
- Two blocks go to two accounts.
- No match → create offered.
- An empty last 4 is filled in from the file.

**Balances and the statement:**
- Opening and closing balances computed; continuity with the previous statement checked, and a mismatch flags it.
- History row shows the QFX badge and the period; deleting as a unit removes its rows.

**Elsewhere:**
- Person-data export and import keep `source` and `fitid`.
- Migration 0028 runs on an old database.
- The preferred-method nudge works for `qfx`.

---

## 10. Ideas for a redesigned Upload → Statements page

How it is now: one long page. The PDF form is at the top. Under it come two collapsed sections (Add a
transaction by hand, Import a CSV), each with a paragraph of help. Then come two separate history tables
(Statement history, CSV import history). Uploading means picking the right form first. With QFX added, it would
be three forms.

**A. One drop zone that works out each file** *(recommended core)*
- One "Drop statements here, or choose files" box takes PDF, QFX/OFX and CSV together.
- Each chosen file becomes a row before anything is sent: an icon by type, the name, the account worked out for
  it (QFX: last 4; PDF: last 4 in the file name if any, else the account picked last time; CSV: its Account
  column or the picked account), and a small account menu to change it.
- One **Upload** button sends them all, and each row then shows its own progress: queued → reading → done, with
  "214 transactions · 3 need review".
- The per-type options appear only when a file of that type is in the list: "Skip confirmation" for PDFs,
  "Header and data rows" for CSVs.

**B. An accounts strip at the top**
- A row of small cards, one per account, each showing:
  - the last period uploaded ("Visa · up to Sep 2026");
  - a gap warning when a month is missing ("Aug missing");
  - the preferred method, and an **Upload** shortcut that opens the drop zone with that account picked.
- This answers "what do I still need to download?" at a glance. Today that needs the Transactions → Accounts tab.

**C. One upload history instead of two tables**
- Statements (PDF and QFX) and CSV imports in one list, newest first. Each row has a type badge, the account, the
  period, the count and a status chip.
- Filter chips: type, account, status. Paging and sort as today.
- **Waiting for you** rows (awaiting confirmation, needs review, errors) pinned above the list as a short action
  list ("Confirm", "Review", "Restart"), not mixed in by sort order.

**D. Tabs instead of collapsed sections**
- Under the drop zone: **Upload files · Add by hand**.
- The long help paragraphs become one line each, with a "How this works" link opening the detail (required CSV
  columns, the sign rules, what happens to the PDF).

**E. Remember what worked, per account**
- After a successful CSV import, offer "Remember this layout for Visa": header row, data row, date column,
  debit/credit. Next time it's applied by itself and shown as a chip ("Using Visa's saved layout · change").
- The same for "Skip confirmation" per account (some banks' PDFs are always read right).

**F. A summary after each upload**
- One card at the end, for example "3 files · 2 statements, 1 CSV · 412 transactions · 9 duplicates skipped ·
  4 need review", with **Review now**. It replaces three per-file sentences and the reload after 1.5 s.

**G. Phone**
- The drop zone becomes a big **Choose files** button, which opens the phone's Files app, where bank downloads
  land.
- History shows as cards (as other pages already do); the accounts strip scrolls sideways.
- The Upload button stays at the bottom of the screen, above the menu bar, while files are listed.

**Suggested order:** A + C first (they make QFX fit without a third form), then B, then E, F and G. D comes
naturally with A.

## 11. Open decisions

1. **Confirmation for QFX:** none by default (§6). Alternative: follow the form's "Skip the PDF confirmation step"
   box like a PDF does.
2. **Statement period:** DTEND's month (§6), or a range-only label when the range spans more than one month.
3. **Should a QFX row's FITID also prevent a later PDF of the same month from adding the same rows?** That already
   happens through the date / amount / description check. FITID only adds anything between two QFX files.
4. **"Match by account number"** as the form's default when only QFX files are chosen, or always the last account
   used.
5. **Redesign scope:** A + C with QFX (one release), or QFX first on today's page and the redesign after.
