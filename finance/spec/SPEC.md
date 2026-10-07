# Finance Dashboard — rebuild spec (v1.0.0)

This spec is enough to rebuild the app. Where it disagrees with the code, the code wins.
It describes **current behaviour only**. The user guide is `finance/DOCS.md` (the app's Documentation tab).
Conventions shared with the sibling apps live in `HA_ADDON_PATTERNS.md`, `WHOAMI_PAGE_SPEC.md`
and `SHARING_AN_ADDON.md`; code comments cite this file's section numbers ("SPEC.md section 6"),
so keep them stable. The database schema, as the migrations leave it, is in
`finance_schema.sql` next to this file.

A self-hosted Home Assistant app for one household. It reads bank and
credit-card statements (PDF or CSV) — PDFs with the AI model the admin chooses
(section 22) — and, optionally, utility bills and toll statements (section
23), and turns them into searchable transactions, dashboards, reports and
comparisons. Each Home Assistant user sees only their own data unless the admin
shares it (section 20).

---

## 1. Deployment and request security

- **Ingress only.** `config.yaml` has `ingress: true` and never a `ports:`
  mapping. `panel_admin: false` (Home Assistant's default is true, which shows
  the sidebar entry to admins only): every HA user may open the panel; what they
  see inside it is scoped (sections 2–3, 20). A change to it takes effect after
  the app restarts and the Home Assistant page is refreshed.
- **Only Supervisor's ingress proxy may connect.** A request whose client
  address isn't `172.30.32.2` (`auth_core.INGRESS_PROXY`, from the shared
  `app/common/auth_core.py`; no loopback here) gets 403 (`app/security.py`,
  `trusted_client_ips()` + `auth_core.from_ingress`) — otherwise any
  container on HA's internal network could send the identity headers itself.
  uvicorn runs with `--no-proxy-headers`, so that address is the real TCP peer.
  The app option `trusted_client_ips` adds addresses (the 403 text names the
  address it saw); `ALLOW_ANY_CLIENT=1` turns the check off for local
  development only.
- **Cross-site requests.** A state-changing request that the browser labels
  `Sec-Fetch-Site: cross-site` or `same-site` is refused. Requests without the
  header are allowed; the random per-session ingress URL is a second barrier.
- **Relative URLs, flat routes.** Every link, fetch and redirect is relative with
  no leading slash (a leading slash escapes the ingress prefix and lands on HA's
  own frontend). Routes are flat with query strings (`account?id=5`, not
  `accounts/5`) so relative links resolve the same from every page.
- **Time zone.** `bootstrap.py` reads Home Assistant's configured zone through
  Supervisor's Core API (`homeassistant_api: true`) and sets `TZ` before
  starting uvicorn; fallback `UTC` (was America/Denver before 1.0.1; not fixed
  MST). The image installs `tzdata`. SQLite `datetime('now')` audit timestamps
  stay UTC.
- **App options** (the app's Configuration tab): `admin_users` (list) and `trusted_client_ips` (list) only.
  The AI address and model are set in the app on Admin → App settings (section 21). A TCP `watchdog` lets Supervisor restart a hung app.
- **Caching.** Every response is `Cache-Control: no-store`, static files
  included (the HA companion app's WebView was seen serving stale, even mixed,
  stylesheets). Static URLs also carry `?v=<version>`.
- **Security headers** (`web_security.SecurityHeaders(CSP, all_no_store=True).install(app)`,
  shared `app/common/web_security.py`, policy `CSP` in `app/main.py`): every
  response gets `Content-Security-Policy: default-src 'self'; script-src 'self';
  style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self';
  font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self';
  frame-ancestors 'self'`, `X-Content-Type-Options: nosniff` and the `no-store`
  above. So the templates carry no inline `<script>` blocks and no `on…=`
  handlers, and no htmx attribute needs `eval` (no `hx-on`, no `js:` values, no
  trigger filters) — see section 13.

## 2. Authentication — Home Assistant identity

No local accounts. Identity comes from `X-Remote-User-Id` (the scoping key) and
`X-Remote-User-Name` (display only), trusted only together with `X-Ingress-Path`
and only because section 1 refuses every other client. Dependency chain:
`get_current_user` → `get_acting_user` → `require_admin`. Every query filters by
the acting user's id, and every write by id first checks ownership.

## 3. Admins, acting as someone else, Storage and backups

- **Admins** are the entries of `admin_users`, matched case-insensitively (stray
  quotes/spaces ignored) against the user id or the name Home Assistant sends
  (`X-Remote-User-Name`); `app/auth.py` is a thin layer over the shared
  `app/common/auth_core.py` (`admin_entries`, `is_admin` with `casefold`,
  `no_admin`, `require_admin_flag`). Being an HA
  administrator is not enough; a changed list needs an app restart.
- **`whoami`** (the name in the sidenav, "how the app sees you") is drawn by
  Finance's own template from the shared data (`app/common/whoami.py`,
  `WHOAMI_PAGE_SPEC.md`): the exact user name and id received, the display name
  (not used for matching), whether the person is an admin and how many admin
  entries exist (never the list), and the advice in the shared wording (admin /
  display name only / empty list with the restart path / no match).
- **No admin yet.** While `admin_users` is empty, `request.state.no_admin` (was
  `no_admin_configured`) is true and `base.html` shows on every page: "No admin
  yet — add your Home Assistant user name (<name>) to admin_users on the app's
  Configuration tab, save, and restart the app. The admin then sets up the AI on
  Admin → App settings." with a link to How the app sees you — the same text as
  every other app plus the AI step.
- **Acting as.** An admin adds `?as_user=<id>` to view and act on one other
  user's data (a non-admin's `as_user` is ignored, except between the users the
  admin shared with them — section 20). Never a blended multi-user
  view — except admin Storage and the full database download below.
- **Switch-user dropdown** (top right, admin only, every page and screen size)
  lists `known_users`: every identity the app has seen in the headers (written
  at most every 10 minutes per user, or at once when a name changes).
- **Storage** (admin, cross-user): every stored statement, utility and toll PDF
  and any leftover CSV file, with size and status; delete one file, or all of
  completed documents' PDFs.
- **Download database:** a consistent copy via SQLite's backup API (WAL-safe;
  `db_core.snapshot(after=_blank_secrets)`), sent by `backup_core.send_file` and
  deleted from the server once sent.
- **Import database** (restore after a reinstall): explicit confirmation; the
  upload is streamed to a temp file next to the database
  (`backup_core.receive_sync`); the file must open as SQLite, pass
  `integrity_check` and contain the core tables (`db_core.validate_file`, the same
  three messages as before); the current WAL is checkpointed and its sidecar files removed, then the file is
  swapped in atomically inside `/data`, and the migrations run on it at once, so a
  backup from an older version gets the newer tables (such as `ai_usage_log`, section
  21.3) before any page or AI request uses them. Restart the app afterwards.
- HA's own backups also cover `/data`.

## 4. Bank statements — upload and processing

1. **Upload** (Upload → Statements): one account, several PDFs per request, and a "Skip
   the PDF confirmation step" box (unchecked by default, section 7). Each file
   must be a PDF of at most 50 MB (`MAX_PDF_MB`). Password-protected PDFs are not
   supported.
2. **One job at a time.** Statements, utility bills and toll statements share one
   in-memory FIFO queue with a single worker (`app/jobs.py`); every vision call —
   admin debug re-sends included — holds one lock, so the local model never does
   two at once. A queued row says how many jobs are ahead; `current_step` shows
   the live step; the row polls every 3 s.
3. **Text pass** (`pdftotext -layout`): rejects a PDF with no text layer (no
   OCR), collects every printed money amount and reads the printed previous and
   new balance. It does not build transactions.
4. **Vision pass** — the only source of transactions. Pages are rendered to PNG
   (`pdftoppm`) and sent to the AI model (section 22) as images, deliberately not
   the text above, so a text-extraction mistake can't agree with itself. With
   Ollama a warm-up call loads the model unless it answered in the last 2
   minutes. Failures (the AI unreachable or refusing the key, bad
   JSON after retries, a non-vision model) end as `error` with a message, never a
   silent fallback.
5. **Save** in one database transaction: rows (after sections 5 and 6),
   categorization (section 9) and transfer auto-linking (section 8). Then
   `finalize_if_ready` decides `complete` (PDF deleted) or `pending_review`.
6. **Statuses:** `processing`, `pending_review`, `complete`, `error`.
   - **Restart** (failed statements only) re-runs the job on the stored PDF.
   - **Re-extract** (any `pending_review` statement) permanently removes its
     unconfirmed transactions and re-runs the job on the same PDF — the model
     isn't deterministic, and a second reading is often right.
     `needs_confirmation` is kept. Re-extract opens a box, **What was wrong?**,
     prefilled with the notes from the last re-extract: whatever is written there
     (up to 2,000 characters) is saved on the statement (`extraction_notes`,
     shown on the debug page and used by its re-send) and sent to the model with
     the PDF, told to follow it over the default rules. Leaving it empty clears
     the notes.
   - **Interrupted jobs:** the queue doesn't survive a restart, so at startup
     every row still `processing` becomes `error` ("Processing was interrupted …
     Click Restart"). Same for utility bills and toll statements.
7. **Admin Debug** (`statement-debug`): steps, skipped lines, raw model response,
   categorization breakdown, and a re-send with no timeout.

## 5. Parsing robustness

- The model's JSON is cut from the first `{` to the last `}`, up to 3 attempts;
  its "thinking" field is used when "response" is empty.
- An amount is any number with exactly two decimals (`.50`, `$.99`, `1,234.50`).
- **Every amount must be printed** in the statement text, or the row is flagged
  "amount not found" (catches a misread like $287.00 → $2187).
- **Reconciliation:** the transactions must add up to the printed balance
  movement within $0.01, or the statement gets a `balance_mismatch` note.
- **$0.00 lines** (e.g. "INTEREST CHARGED ON …" with nothing charged) are
  dropped and noted — never inserted (`amount != 0` is a table constraint).
- On a credit card, a nonzero negative "interest charged" line is flipped to
  positive (interest increases what is owed).
- **Credit-card prompt.** A credit-card statement is sent with its own prompt:
  purchases, fees and interest positive, payments, credits and refunds negative
  (section 8), so a card's charges come out as money out.
- **Card signs backwards.** If a card's extracted transactions add up to exactly
  the negative of the printed balance movement, the model signed every line the
  wrong way round: all signs are flipped, the statement reconciles, and a
  "Signs flipped" note is kept with the statement's other notes. The payments'
  wording decides first, though: every line worded like a card payment
  ("payment", "autopay", "thank you") positive and most other lines negative
  flips every sign, with the same note — and if the printed balances seemed to
  agree with the backwards reading, the balance was read without its sign (a
  credit balance), so its movement is taken the other way round and the
  statement still reconciles. A printed balance keeps its sign: "-$14.01",
  "$-14.01", "($14.01)", "$14.01-" and "$14.01 CR" are all a credit of 14.01.
- Sign convention: section 8.

## 6. PDF storage and duplicate detection

- Files live in `PENDING_DIR/<user>/...` (`/data/pending`, `app/storage.py`)
  only until the document is finished; a stored path is only ever served or
  deleted if it is inside `PENDING_DIR`.
- Every duplicate check looks at **live rows only** (a soft-deleted statement
  never blocks a re-upload; `file_hash` is a plain index, not UNIQUE).
  1. **Same file** (SHA-256): refused, or — when the earlier upload failed —
     pointed at Restart.
  2. **Already on file:** a transaction with the same account, date, *signed*
     amount and description (from a PDF or a CSV) is skipped; it still counts
     toward reconciliation, because the balance really moved.
  3. **Repeated inside one extraction** (a totals line read as a transaction):
     skipped and not counted toward reconciliation.
  4. **Nothing left to insert:** the statement fails as "Duplicate — …" and
     keeps its PDF so Restart remains possible.
- Everything skipped (and every dropped $0.00 line) is listed on the Debug page.
- **Insert anyway.** Every line skipped by rule 2 or 3 — from a statement or a
  CSV import, where a row repeated inside the same file is also skipped — is
  kept in `skipped_duplicates` and listed on **Upload → Review** under
  "Skipped as possible duplicates" (date, description, amount, account, source,
  why, and a link to the matching stored transaction). **Insert anyway** adds it
  as a clean transaction of that statement or import (category: the CSV's own,
  "Payments" for a card payment, else the rules — no model call), then runs
  transfer auto-linking; **Keep skipped** takes it off the list. The statement's
  Upload row shows "N skipped as duplicates", linking there. Re-extract and
  Restart replace a statement's skipped lines; deleting the statement or import
  hides them.
## 7. Confirmation and review

- **Confirmation step** (unless skipped at upload): a parsed statement waits as
  "Awaiting confirmation" and keeps its PDF. `statement-confirm` shows period,
  count, money in, money out (credit-card totals swapped), printed balances, the
  PDF and every transaction with **Remove** — a permanent delete, allowed only
  on an unconfirmed statement, after which the balance check is recomputed.
  **Confirm** sets `confirmed_at`; the statement completes once nothing else is
  pending. Confirm is also offered in the PDF viewer and on the Uploads row.
- **Needs review** (flagged rows and/or a balance mismatch): the Uploads row
  links to `review?statement_id=<id>` — the Review page narrowed to that
  statement (without the parameter it shows everything).
- **Review page:** balance-mismatch statements (Acknowledge, or the confirm page
  when confirmation is needed) and flagged transactions, each confirmed or
  corrected against the PDF (`transaction-resolve`); bulk approve, categorize,
  delete. Review is confirm-or-correct, since only the vision pass produces
  values.
- Statement history's default order puts "Awaiting confirmation" first.

## 8. Accounts, sign convention, transfers, exclusions

- Account types: `checking`, `savings` (assets) and `credit_card` (liability).
- **Sign convention.** Checking/savings: negative = money out, positive = money
  in. Credit card is the mirror: a purchase is positive (it increases what is
  owed), a payment or refund negative. Matching, categorization, the dashboard,
  recurring detection and CSV Debit/Credit import all follow it. The type is not
  editable after creation for this reason.
- **Transfers:** an outflow on one account matched to an inflow on another,
  same amount (±$0.01) within 5 days (`app/matching.py`). On a credit card the
  receiving side is the negative payment line; a card purchase is never part of
  a transfer. Unambiguous matches link automatically after an import; ambiguous
  ones wait on the Transfers page. Both rows are linked in one database
  transaction. Linked pairs count as neither spend nor income.
- **Unlinking, or deleting one side,** reverts the survivor: a checking side
  becomes an expense; a card side becomes income with `is_excluded` set, so it
  neither inflates income nor gets re-linked by the next sweep.
- Transfers page: date and from/to filters, defaulting to this month; "check this
  transaction" and a whole-user sweep. **Link this** on a waiting candidate links
  the pair and returns to the same filtered view with "Linked: …"; if the pair
  can't be linked (a side already linked, deleted, pending review, the wrong
  sign or account type, or both on one account) it returns with a message
  saying which side and why — never a bare error page. New credit-card payment rows are
  categorized "Payments" on import.
- **`is_excluded`** keeps a real transaction visible everywhere but out of
  dashboard and trend totals (reimbursements, one-off gifts, transfer legs).
  Totals say "Excludes $X across Y transactions" when it applies.
- **Notes** on a transaction are searchable and only allowed once the row is
  clean (a table constraint), since a flagged row may still be corrected.
- Ledger dates are the transaction date (post date only when that's all there
  is), stored as ISO text.

## 9. Categorization

Order: your own rules → built-in merchant rules → one batched LLM call for the
rest → "Uncategorized" (never a silent guess). Rules match by specificity
(exact > prefix > substring, then longer pattern, then newest). Every row
records how its category was decided (`category_source` and the matched
pattern); the categorization debug view shows it for PDF and CSV imports alike.
Saving a rule can re-categorize all existing transactions; custom categories can
be added and removed. An unreachable or failing LLM only leaves rows
Uncategorized — it never fails an import.

## 10. Manual entry, CSV import, CSV export

- **Manual entry** (Uploads, collapsed): one transaction; blank category →
  "Uncategorized" (no LLM call on an interactive form).
- **CSV import** (Uploads, collapsed): several files per request, each at most
  20 MB (`MAX_CSV_MB`), each recorded as one `csv_imports` row whatever happens.
  The file sits in `PENDING_DIR/<user>/csv-imports/` only while importing (a
  leftover shows in Storage).
  - Required columns: Date, Description, Amount (or both **Debit** and
    **Credit**), plus Account unless an account is picked on the form. A missing
    column rejects the whole file. Unknown columns are ignored; Description is
    used, never Merchant.
  - **Date column:** "Date" wins; else a single column containing "date"; else
    the one containing "trans"; else the page asks which to use and resubmits
    that file. Dates are normalised to ISO; an unreadable date fails that row.
  - **Debit/Credit:** the column gives the direction (checking/savings: debit
    out, credit in; credit card: debit = purchase, positive; credit =
    payment/refund, negative). Both or neither filled is a row error.
  - **Status column** (optional): only "posted" or "cleared" rows (any case) are
    imported; others are counted as skipped.
  - Duplicates (same account, date, amount, description — earlier rows of the
    same file included) are skipped and listed.
  - Blank-category rows go through section 9 in one batch.
  - Results show inline; the history shows account(s), first/last date and a
    result badge; admins get a read-only debug page. A CSV import can be deleted
    as a unit (section 11).
- **CSV export** (Transactions page) exports exactly what the filter form holds,
  streamed line by line (`csv_export.stream`, shared `app/common/csv_export.py`).
  Text cells starting with `= + - @` (or tab/CR) get a leading `'` so
  spreadsheets don't run them as formulas (`csv_export.guard`); the file is byte
  for byte what it was.

## 11. Account management, deleting, restoring, purging

- Setup → Accounts: "+ Add account" (collapsed, at the top), then the list with
  type, bank, last 4, preferred upload method, Archive/Unarchive and Delete.
- Transactions → **Accounts** tab (`transactions-accounts`): every live,
  unarchived account with its **latest transaction** (newest live transaction
  date, "—" when none), transaction count and preferred upload method, oldest
  first so the accounts due a new statement are at the top; links to the
  account's transactions and to Upload.
- **Edit details** (account page): name, bank, last 4 (sanitised server-side to at
  most four digits). Names are read live everywhere, so a rename needs no
  re-import — but a multi-account CSV's Account column matches today's name.
- **Preferred upload method** (PDF / CSV / either) is advisory: a mismatched
  upload shows a warning with "Upload anyway".
- **Website** (optional, editable on Add account and Edit details): the bank's
  login or statements page. Only `http(s)://` addresses are kept (`https://` is
  added when no scheme is typed; `javascript:`, `data:` and the like are
  dropped); blank clears it. Shown as an "Open ↗" link, opening in a new
  window (the phone's browser in the Home Assistant app), on Setup → Accounts,
  the account page and Transactions → Accounts.
- **Archive** hides an account from the default list and current-period
  dashboard; one click to reverse.
- **Soft delete with a 30-day grace period** for a statement, a CSV import (its
  transactions with it), a transaction, a utility bill, a toll statement (its
  passes with it), an account (its live statements and transactions with it;
  refused while one is processing) and, for the admin, **Wipe all my data**
  (everything the acting user owns, utility bills and toll statements included). Rows deleted together share
  one timestamp, so Restore brings back exactly that batch.
- Destructive actions show counts first and need something typed (the account
  name, the file name, the user's display name, or the word `delete`).
- **Recently deleted is admin only**: the nav item, the page, every Restore,
  **Purge** (erase now) and Wipe all my data. Everyone can still delete; the
  confirm pages say the admin can restore it within 30 days. The admin uses the
  user switch to restore someone else's items.
- A daily sweep (`app/purge.py`) erases anything past 30 days, with its PDFs; a
  PDF shared by two utility bills stays while either still exists; an account is
  only purged once nothing live points at it.

## 12. Tech stack and data

- Python 3.13, FastAPI, Jinja2 + htmx 2, raw `sqlite3`, poppler's
  `pdftotext`/`pdftoppm`, stdlib `urllib` (the AI providers) and `csv`. Dependencies are
  pinned in `requirements.txt`; add one only when the stdlib can't do it.
- SQLite in WAL mode with `busy_timeout` (5000), `foreign_keys` and `synchronous=NORMAL`
  on every connection (`get_db()` = `db_core.connect` from the shared
  `app/common/db_core.py`, connection class `db._Connection` =
  `db_core.ClosingConnection`); `with get_db() as conn` commits and closes.
- **Migrations:** `migrations/NNNN_*.sql`, applied in order at startup and
  recorded in `schema_version` (each file inserts its own row). Never edit an
  applied migration — add a new file. A migration that leaves foreign-key
  violations is not recorded.
- Dates are ISO text so string order is date order; legacy non-ISO dates are
  normalised at startup.
- Ollama is usually plain HTTP on the LAN, so statement page images cross the
  local network unencrypted; point its address (Admin → App settings) at an HTTPS
  reverse proxy if that matters. Cloud providers are HTTPS.

## 13. User interface

- **Transactions page.** Nothing is queried on a plain page load. **Search** (or
  Enter) runs the query with whatever the form holds and always sends
  `searched=1`, so "All accounts" with nothing else set shows everything; links
  arriving with a filter (dashboard drill-downs) run at once. Filters: note text,
  description contains, account, category, money in / money out (account-type
  aware, matching the dashboard), excluded (show all / excluded only / hide
  excluded; with money in or out, "excluded only" shows the excluded rows of
  that direction), date range, amount range. CSV export follows the same
  filters. A running total of
  what's shown. Per row: category (with save-as-rule), exclude, note, "find
  recurring" lookup, link to the transfer partner. **Select multiple…**
  (collapsed): categorize, exclude, un-exclude, delete; a bulk action returns to
  the same filtered view. The form has no submit button on purpose — a form's
  only submit button is triggered by Enter in any field (it once silently fired
  Export).
- **Six sections.** The nav has Home, Transactions, Upload, Bills, Reports, Setup
  and (admin only) Admin; each page has a tab bar for its section:

  | Section | Tabs | Pages |
  |---|---|---|
  | Home | Overview · Compare | `dashboard`, `dashboard-compare` |
  | Transactions | All · Transfers · Recurring · Accounts | `transactions`, `transfers`, `recurring`, `transactions-accounts` |
  | Upload | Statements · Review · Utility bill · toll statement | `uploads`, `review` (Back returns to Statements), `utilities?tab=upload`, `tolls?tab=upload` (each with its history) |
  | Bills | Utilities (Dashboard · Compare) · Tolls (Dashboard · Compare · Analyze) | `utilities`, `utility-comparison`, `tolls`, `toll-compare`, `toll-analyze` |
  | Reports | — | `reports`, `report` |
  | Setup | Accounts · Categories | `accounts`, `account`, `categories` |
  | Admin | Storage · Query · Users · Recently deleted · AI usage · App settings | `admin-storage`, `recently-deleted`, `ai-usage`, `settings` |

  A page's section comes from its `active_page` (utilities/tolls pages count as
  Upload on their upload tab, Bills otherwise). URLs are unchanged.
- **Staying in Upload.** Debug, review and PDF pages of an upload belong to the
  Upload section, and their Back link returns to that upload page (Statements,
  Utility bill or toll statement). Actions on an upload — upload, delete,
  resolve, restart — come back to the same upload page, never another one. The
  Upload nav item reopens the upload page used last (remembered per browser).
- **Upload → Statements:** statement upload, manual entry and
  CSV import; statement history (money in/out, first/last transaction, status
  actions: Open PDF, Confirm, Review, Re-extract, Restart, Delete, admin debug)
  and CSV import history.
- **Themes:** midnight (default), slate, daylight and auto (Daylight on a light
  device, else Midnight), shared with the other household apps: the colours come
  from `static/common/themes.css` (`static/style.css` adds the green accent and
  Finance's own colours), and `static/common/theme-boot.js` applies the saved
  `theme` in `<head>` before paint (also in `pdf_layout_embed.html`). The old
  `sandstone` maps to daylight. Pickers in the sidenav and the phone **More**
  panel (`HouseholdTheme.bindSelect`, from `static/base.js`). The collapsed
  sidenav keeps its own `navCollapsed` key and `nav-collapsed` class, re-applied
  in `<head>` by `static/nav-boot.js`.
- **No inline scripts** (CSP `script-src 'self'`). `base.html` loads
  `static/common/theme-boot.js`, `static/nav-boot.js` and `static/htmx.min.js` in
  `<head>`, and `static/base.js`, `static/common/ui.js` and `static/ui.js` at the
  end of `<body>`. `static/base.js` holds every page's own behaviour (the user
  switches, the phone More panel, the Upload nav memory, the sidenav collapse,
  the theme pickers, the re-extract dialog, the bulk actions) and delegated
  replacements for the old `on…` attributes: `form[data-confirm]`,
  `select[data-autosubmit="<names to disable>"]`, `select[data-go]`,
  `select[data-click-sibling]`, `select[data-go-param]`, `[data-export]`,
  `form[data-enter-clicks]`. Page scripts are `static/pages/confirm-text.js`,
  `resend.js`, `dashboard.js`, `settings.js`, `pdf-view.js`, `toll-upload.js`,
  `utility-upload.js`, `statement-upload.js` and `csv-import.js`, with the
  server's values in `data-*` attributes (JSON through `tojson`). The saved-report
  id after a Query save is a hidden `data-saved-report-id` span that
  `static/query.js` applies; the Query variables box listens for the custom event
  `q-def-type-change` sent by `query.js` (it was an htmx trigger filter).
- **Navigation:** the sidenav collapses to icons on desktop; on phones it's a
  fixed bottom bar with Home, Transactions, Upload, Bills, Reports and **More**
  (Setup, Admin for the admin, "how the app sees you", theme, version).
- **Tables:** every table sorts by clicking a header (dates, numbers and text
  detected per column; phones get a sort select). Long histories (statements,
  CSV imports, utility and toll uploads) are sorted and paged on the server
  (10/20/50/All, state in the URL with a per-table prefix).
- **Nothing stays stuck:** Back/Forward loads the full page, polling refreshes
  whole rows, a failed request shows a "Reload page" banner, a bfcache-restored
  page reloads.
- Text inserted into HTML from JavaScript goes through `escapeHtml`
  (`window.escapeHtml`, set by `static/ui.js` from the shared
  `static/common/ui.js`'s `UI.escapeHtml`, which also escapes quotes).

## 14. Operational discipline

- Bump `config.yaml`'s `version` on every rebuild (Supervisor compares it as a
  string); it also busts static-file caches and shows in the sidenav, which tells
  "not redeployed" apart from a real bug.
- Run `python3 -m pytest` (in `finance/`) before every release; add a test for
  every fix.
- Commit to git with a message saying why; the history replaces the old
  round-by-round notes.
- If financial data is ever pushed to HA as sensors: aggregates only.

## 15. Utilities (separate from finance)

Utility bills never touch accounts or transactions (no matching a bill to its
checking-account payment).

- **Data:** `utility_bills` — type (electric/gas/water), provider, exact billing
  period dates (cycles don't follow calendar months), usage and unit, cost
  (including every pass-through fee, which aren't itemised), due date, last 4,
  and the same lifecycle columns as statements. Electric bills also have
  `electric_meter_details`: grid import, solar export, net delivered and net
  generated, each on-peak / off-peak / total, plus on/off-peak rate and charge.
  Total solar generation never appears on a bill and isn't modelled.
- **Upload:** provider chosen on the form (Xcel Energy / Aurora Water), several
  PDFs, same size/type limits and queue as statements. A PDF with no amounts, or
  that never names the chosen provider in full ("xcel energy", "aurora water"),
  is rejected before the vision call.
- **Parsing:** the vision pass extracts the bill(s); one Xcel PDF can hold
  electric and gas and becomes two rows sharing the file. Seasonal rate changes
  are summed into one bill; Aurora's tiers are stored as totals. Each bill's cost
  must be printed in the PDF text — and for dual-fuel the combined total too — or
  the bill waits for review.
- **Duplicates:** same file hash; the same provider/type/period/last-4 already
  live; or listed twice in one PDF. All duplicates → the upload fails and keeps
  its PDF. Restart first removes what the failed run saved.
- **Review** (`utility-review`): confirm or correct the values (meter details
  included); resolving deletes the PDF.
- **Tabs:** Dashboard (default; provider/type/period filters; electric with the
  full meter detail, gas and water usage and cost; re-polls while anything is
  processing), Upload (form + paged, sortable history with status, delete, admin
  debug), Compare (gas/water charts with a table fallback, electric table, two
  chosen months side by side with deltas; unconfirmed bills included and marked).

## 16. Recurring charges

- Money out only. The unit is a **series**: one merchant family on one account
  at one amount (a merchant taking two amounts each month shows as two lines).
- Merchant key: digits, punctuation and masked numbers stripped, first 32
  characters; on one account a specific key (3+ words or 20+ characters) that is
  a word-prefix of another joins its family.
- Amounts: exact repeats anchor a series; others join within 20% or $3; the rest
  cluster (a variable bill shows a range).
- Cadence: weekly (7 ± 3 days), monthly (30 ± 6), quarterly (91 ± 12), annual
  (365 ± 20); at least half the gaps one period and 80% fitting; skipped periods
  (2–3) allowed except weekly.
- 3+ charges on a cadence = recurring; 2 = "possibly recurring" (confirm or
  dismiss). Detection runs at query time; nothing is stored per row.
- Overrides (`recurring_overrides`) per merchant family or per amount series
  (series wins): mark recurring (even from one charge), dismiss, back to
  automatic. "Find recurring in all transactions" re-scans; each transaction has
  a lookup modal.
- Filtering to one card gives the card-migration view: recurring charges on that
  card and, separately, every merchant that ever charged it.
- A totals card groups active charges by cadence with per-month and per-week
  equivalents.

## 16a. In-app PDF viewer

- No page links to a raw PDF: "Open PDF" opens `pdf-view` in an in-page modal (or
  as a page), because the HA phone app hands a new-tab link to a browser with no
  session. Pages are PNGs rendered at 200 DPI by `pdf-page`.
- Highlights money in (green) and money out (red), a bill's total or a toll
  statement's Grand Totals on every page where printed, lists those pages, and
  says so when a figure isn't printed. Search box. Confirm when the statement is
  awaiting confirmation.
- The toolbar (figures, search, Confirm) is a sticky header; page-jump links land
  below it.
- Pinch-to-zoom (1×–4×) is handled by the page itself — the viewer is often
  several iframes deep, where native pinch is unreliable; one-finger scrolling
  pans.

## 16b. Dashboard and Compare

- Periods: this week, this month (default), last month, this year, last year, all
  time, a specific month or year; optional account filter.
- Income (checking/savings inflows) and Spend (account-type aware; transfers and
  excluded rows out), each by category, with drill-down into Transactions.
- **Compare** (`dashboard-compare`): two months or two years side by side, income
  and spend by category, with change and percent change.

## 17. Non-goals and open points

- Not planned: bank logins or aggregators, budgets or forecasting, OCR for
  scanned PDFs, a general-purpose bank-CSV parser, itemised utility fees, total
  solar generation, linking tolls or bills to bank transactions.
- Open: the confirm page has no "numbers don't match" outcome (fix rows through
  Transactions or Review); recurring and toll-trip constants are tuned on limited
  real data.

## 18. toll statements (separate from finance and utilities)

### 18.1 The statement

A summary block and, per device ("Transactions For Device # … Plate #
PLATE-ST"), one line per pass: date/time, agency, road, plaza, lane, direction,
a Toll Status column (read past, never stored) and a positive amount. The plate
is split on its last hyphen (plate, state). The only summary figure used is
**Grand Totals** (its absolute value); Total Tolls, Previous Balance,
Adjustments and Payments are ignored. A pass line whose amount is negative or
$0.00 is an unparsed line.

### 18.2 Pages

Nav item **Tolls**: Dashboard, Upload, Compare and Analyze tabs; Review and admin
Debug per statement; PDFs through the viewer (`kind=toll`).

### 18.3 Data

`toll_statements` (same lifecycle columns as utility bills, plus the printed
Grand Totals in `total_tolls_printed`), `toll_transactions` (one per pass, with a
`dedupe_key`), `toll_devices` (found on statements; the plate follows the newest
statement), `toll_cars` (created by the person; devices are assigned to them),
`toll_trips` (a cache, rebuilt), `toll_patterns` (tags), `toll_pattern_groups`,
`toll_trip_overrides` (manual per-trip choices, keyed so they survive rebuilds).

### 18.4 Parsing and checks

Two readings are compared: the deterministic text reader
(`toll_deterministic.py`; every date-and-time line it can't parse is recorded as
an unparsed line) and the vision reader (asked for date and time as printed, with
AM/PM; the 24-hour conversion happens in code). AI lines filed under a car with
no device and no plate continue the heading above them. Checks ($0.005
tolerance): the passes agree (device, moment, amount) and so do the cars; no
unparsed lines; the passes add up to Grand Totals; both readings read the same
Grand Totals; per-device subtotals match when printed. All pass → `complete`
(PDF deleted); otherwise `pending_review`, each failed check listed with its
numbers, and held passes count nowhere until confirmed. Duplicates: the same file
is refused; a pass another live statement already holds (same device, moment,
road, plaza, lane, direction, amount — plate ignored) is skipped and counted; all
duplicates → error, PDF kept.

### 18.5 Upload tab, devices and cars

Upload form and paged history (period, cars, passes, the passes' total against
the printed Grand Totals, status actions). **Cars** and **Devices** sections,
collapsed, with counts (unassigned devices highlighted). Devices are added
automatically; cars are created, renamed, archived and deleted by the person;
each device is assigned to a car or left unassigned (then shown as itself
everywhere). Deleting a car keeps its devices and passes.

### 18.6 Dashboard

Car and period filters; the total; one row per tag group plus ungrouped tags,
"Not tagged" and the total; by car; a monthly chart; the paged toll list.

### 18.7 Compare

Two months or years with "By group over time" and "By car over time" stacked
charts covering every period between them.

### 18.8 Analyze: trips and tags

A pass within 45 minutes of the previous one, same direction, joins the trip
(per car, or per unassigned device). Suggested regular trips: the same route,
start times clustered within 60 minutes, **more than 30 trips**, on 2+ days of
some week; window = earliest − 15 min to latest + 15 min. Tag or ignore a
suggestion; a tag matches any car, now and in future statements, including a
contiguous part of its route; groups combine tags; a manual per-trip choice beats
the automatic match. The constants live at the top of `toll_trips.py`.

### 18.9 Review and Debug

Review: Grand Totals against the passes, the failed checks, both readings side by
side with a sentence explaining why a pass is missing from one (a different
device, an unreadable line, not in the PDF text, or not returned by the AI);
per pass: deterministic / AI / edit / leave out; up to three passes can be added;
Grand Totals can be corrected. Confirm completes the statement. Debug (admin):
extracted text, both readings, the check table, unparsed lines, timings and a
re-run of the AI.

## 19. Query tab and Reports

### 19.1 Where and who

- **Storage → Query tab** (`admin-storage?tab=query`): admin only. The admin writes read-only SQL over the data of the acting user (themselves, or the user picked in the switch), sees the results, and saves queries as reports. The Storage page has three tabs: Storage (the default), Query and Users (section 20).
- **Reports page** (`reports`, `report?id=N`): in everyone's nav, after Tolls. Anyone can run any saved report; each run uses the data of whoever runs it. For a regular user that is their own data, or the shared data they're using (section 20); for the admin it is the acting user. Only the admin creates, edits, duplicates or deletes reports, runs "Check reports", or sees a report's SQL. A report that fails shows other users a plain "ask the admin" message; input mistakes ("must be a number") are shown to everyone; SQLite's error text only to the admin.
- Writing SQL is desktop-first (wide editor, Reference box beside it); the Reports list and run pages are laid out for phones too (stacked form, results as labelled cards, a sort dropdown).

### 19.2 How a run is kept read-only and scoped (`app/query_engine.py`)

- Every run gets a private in-memory database. The real database is attached read-only, only the acting user's live rows of the registered tables are copied in (one snapshot), and it is detached before the SQL runs. The SQL therefore can't see anyone else's rows or the real file, whatever it says (`main.transactions` just names the copy).
- An authorizer then allows only SELECT, reads and ordinary functions: no PRAGMA, ATTACH, writes, schema changes, `sqlite_master` or `load_extension`. The connection is also `query_only`. One statement per run; it must start with SELECT or WITH.
- **Registry.** `REGISTRY` lists each queryable table with its short description and the rule that scopes it to one user. Hidden, never copied: `known_users`, `schema_version`, `sqlite_sequence`, `saved_reports`. A test fails if a table is in neither list, so a table added later stays hidden until registered.
- **Deleted rows never appear.** A row is left out when it or its parent is soft-deleted: statements and transactions of a deleted account; transactions of a deleted statement or CSV import; meter details of a deleted utility bill; toll transactions of a deleted toll statement. `user_id` and `deleted_at` are not copied.
- `transactions_with_account` is a convenience view: live transactions with `account_name`, `account_bank`, `account_last4`.
- **Limits:** 10 s per run unless "No time limit" is on (19.6); SQL up to 20 KB; at most 100,000 rows (cut with a note); a result over 64 MB is refused; single values capped at 10 MB (Python 3.11+). Runs happen in a worker thread, never on the event loop, and never in the Ollama job lock.
- Variables reach SQLite as bound named parameters, never pasted into the SQL.

### 19.3 Query tab

- SQL editor (Run, Ctrl/⌘+Enter, Clear, Tab indents); the last query is remembered per browser.
- **Variables panel.** Every `:name` in the SQL appears with its definition (type, label, default, Allow All) and a value box for this run. A pair `:x_start` + `:x_end` is one period variable `x`. Types are suggested from the name (`account`, `period_start/_end`, `category`, `since`/`*_date` → date, `min_*`/`max_*` → number, else text).
- **Reference box**, docked on the right and minimized by default (open/closed and the last section are remembered per browser), with four sections: **Tables** (every visible table and view, its description, row count for the acting user, columns; Preview runs `SELECT * … LIMIT 100`; clicking a name inserts it), **Dates** (how each date column is stored, with format and grouping snippets), **Variables** (each type, its parameters and a ready-made NULL-tolerant filter), **Tips** (money in/out, dashboard-style exclusions, rounding). Every snippet has an Insert button.
- **Save as report / Report settings:** name (unique, case-insensitive), description, totals on/off and columns to leave out, chart type/X/Y/keep signs, No time limit.
- Paging, page size and sort of a Query-tab result happen in place (not in the URL).

### 19.4 Results (shared by both pages)

- Status line: row count, time, whose data; Download CSV.
- **Pagination** above 100 rows: 100 (default), 250, 500 or All, remembered per browser; First / Prev / "Page 2 of 7 · rows 101–200 of 653" / Next / Last. "All" warns above 5,000 rows.
- Sorting by a header (or the phone sort dropdown) sorts the whole result; empty values last.
- **Totals row** sums every all-number column except `id`/`*_id`/`*_ref` and listed columns, over the whole result, rounded to cents, on every page.
- Money-looking columns (name has amount/total/cost/balance/charge/spent/income/paid/price/money/net, or all values are cents) use money formatting; negatives in red. Text over 200 characters is cut with a click to expand.
- The last result is kept in memory for 10 minutes, per person, acting user, query and values (64 MB overall, oldest dropped first). Paging, sorting, chart and CSV reuse it; for a report whose result expired, the page simply runs it again (a no-time-limit report asks you to press Run).
- **CSV:** the whole result in the current sort, no totals row, formula-injection escaping on text cells (`csv_export.guard`, written by the shared `app/common/csv_export.py`), file `<report-name>-<date>.csv`.

### 19.5 Saved reports (`saved_reports`, migration 0017)

- Columns: name, description, sql, variables_json, totals_json, chart_json, no_time_limit, created_at, updated_at, last_run_at. App-wide, not per user: a user's "Wipe all data" leaves them; the admin DB download/import includes them.
- Report page: the variable form, then results; a normal report runs as soon as it opens with its defaults. Links carry the values (`report?id=3&v_account=5&v_period=last_month`), so they can be bookmarked; the same link opened by another user shows their own data.
- **Starter reports:** Spending by category (account, period), Money in / out by month (account, period), Top 20 merchants (account, period, category), Utility cost by month (period). No toll report: the Tolls tab covers that.
- **Check reports** (admin) runs every report with its defaults on the acting user's data and lists any that fail.

### 19.6 Charts and No time limit

- **Chart** (per report, or tried from the Query tab settings): bar or line; X = one column, Y = up to five numeric columns (default: all numeric ones). Several series sit side by side (bars) or as separate lines, colours by series. Amounts are drawn as positive unless "Keep signs" is on, which draws a zero line with negatives below. Skipped with a note above 500 points. Server-side SVG from `charts.py`.
- **No time limit** (Query-tab checkbox, or a report setting shown as "Can take a while"): the run goes to the background and the page polls every 3 s ("Running… 1 min 12 s"), so no request is held open. Only one runs at a time across the app; starting another says so (your own: cancel it first; someone else's: try again, and the admin can cancel it). Cancel stops it at once; a restart also ends it. A no-time-limit report waits for Run, never runs inline, and a link from another site never starts one. While a long read is open SQLite's WAL can't fully checkpoint, so cancel runs you no longer need.

### 19.7 Write SQL from plain English (`app/sql_assist.py`)

- Query tab only (admin). A box above the editor takes a question ("How much did I spend on dining each month?") and **Write SQL** asks the AI (the text model if one is set, else the model; Admin → App settings) to write the query. "Use report variables" (on by default) asks for `:account`, `:period_start`/`:period_end`, `:category` filters written so NULL means All.
- **Never interrupts a PDF.** Before anything is sent the app checks that no uploaded PDF is being read or waiting; if one is, it says so and offers **Try again**. While the request runs it holds the same lock as PDF parsing, so no PDF starts meanwhile. Only one request at a time.
- **Says "hi" first** (Ollama only): a tiny call that confirms Ollama answers (and loads the model; allowed 2 minutes). If it doesn't answer, the error says so with **Try again**.
- **What the model is told:** every visible table and view with its description, columns and types and the acting user's row counts, their account names and categories, the sign/date/transfer conventions, and the variable patterns. Nothing else of their data, and nothing about other users.
- The SQL that comes back is checked by preparing it against the acting user's copy (all variables NULL). If SQLite rejects it, the error goes back to the model once for a fix; if it still fails the SQL is shown with the remaining error.
- The SQL is put in the editor (with **Undo** to restore what was there) and the variables panel follows; nothing runs until **Run** (or **Run it**). Every failure — busy, no answer, bad JSON, timeout (5 minutes) — offers **Try again**.

## 20. Users tab and shared access

- **Storage → Users tab** (`admin-storage?tab=users`, admin only) lists everyone who has opened the app (`known_users`): name, Home Assistant id, last seen, and whether they're an admin.
- **Shared access.** For each regular user the admin can add one or more other users whose data they may use (`user_access`: viewer, owner, who granted it, when), and remove any of them. Typical use: a household member uses the statements already uploaded instead of uploading them twice. Admins can't be given shared access; they already use the user switch.
- **Full rights.** With shared access the viewer works on the owner's data exactly as the owner would: uploads, CSV imports, edits, confirmations, deletes all land in the owner's data. Nothing admin-only opens up (Storage, Query, Users, Recently deleted and restores, debug pages, report editing).
- **Opens straight into it.** A viewer with shared access lands in the first-granted owner's data (no `?as_user=`). A switch at the top right lists those owners and "My own data"; a banner says whose data it is, with a link to their own. `?as_user=` naming anyone not granted is ignored (they get the data they open into). Removing access takes effect on their next page.
- Reports and everything else follow the same rule, because every route works on the acting user (section 2).

## 21. App settings and AI usage

### 21.1 Admin → App settings (`settings`, admin only)

- An App settings tab on Admin (and a link under Admin in the More panel), for the admin only; a regular user gets 403. The settings are app-wide, not per user, whoever the admin is acting as.
- Stored in `app_settings` (key, value, updated_at, updated_by; migration 0022) and read on every use, so **a change applies at once, without restarting the app**: the next PDF job, CSV categorization, debug re-send or Write SQL request uses it; nothing already running is interrupted.
- **AI:** provider, address, model, access key, text model, longest answer (section 22).
- **AI usage on debug pages:** Show AI token usage (on by default) and prices per million input and output tokens (default 0; a local model costs nothing but time). The prices are also used for the cost on Admin → AI usage (section 21.3).
- **Features:** Utilities and tolls on or off (section 23).
- **Not app options.** The AI settings are set only on this page (keys `ai_*`; migration 0023 renamed the earlier `ollama_url` / `ollama_model`). An older install's leftover `ollama_url` / `ollama_model` in `/data/options.json` (or `OLLAMA_URL` / `OLLAMA_MODEL` in the environment, for local development) fill in the address and model once if they're still empty ("Copied from the old app options"; the stored `updated_by` marker stays `add-on options`). While the AI isn't set up (no model; Ollama without an address; Claude without a key) App settings and the Statements, Utility bill and Tolls upload pages say so (the admin gets a link to App settings), and a PDF job fails with the same message.
- Saving checks every value first; if one is wrong (bad address, unknown provider, Ollama without an address, a longest answer outside 256–200,000, negative or non-numeric price) nothing is saved and the form keeps what was typed, with the errors listed. Each field shows when it was last changed and by whom.
- New settings are added to `app/settings.py` (`SETTINGS` and a `GROUPS` entry) and appear on the page automatically (kinds: url, text, bool, price, int, choice, secret).
- `app_settings` and `ai_usage_log` are hidden from the Query tab.

### 21.2 AI usage per request (`app/ai_usage.py`)

- Every request to the model is recorded: purpose (Warm-up, Extraction (attempt n), Categorization, Debug re-send, Hi / Write SQL / Fix SQL), model, input tokens and output tokens as the provider reports them (Ollama `prompt_eval_count` / `eval_count`; OpenAI-compatible `usage.prompt_tokens` / `completion_tokens`; Claude `usage.input_tokens` plus cache reads and writes / `output_tokens`), time (Ollama's `total_duration`, else measured) and, for a failed request, the error.
- A PDF job (statement, utility bill, toll statement) stores its requests on its row (`ai_usage` JSON), including the categorization it triggers; a re-run replaces the earlier run's. A CSV import stores its categorization request on `csv_imports.ai_usage`.
- Shown, when Show AI token usage is on, as **AI usage — last processing run** on the statement, utility bill and toll debug pages (**AI usage — categorization** on the CSV import debug page): one row per request, with a totals row when there are several. A debug re-send shows **AI usage — this re-send** for that call. Write SQL shows one line with requests, tokens, time (and cost).
- **Cost** = input tokens × input price + output tokens × output price, per million, at today's prices on Settings; with both prices 0 it shows "—".

### 21.3 Running total (Admin → AI usage, `ai-usage`, admin only)

- **Every request is also added to `ai_usage_log`** (migration 0024): time (UTC), purpose, provider, model, input and output tokens, seconds, error. Unlike a document's `ai_usage`, which keeps only its last run, the log keeps every request, so the total counts every run and re-send, Write SQL and failed requests, and is never lowered by a re-run, a delete, the 30-day purge or wiping a user. It holds no user ids or document ids.
- **Recording never holds up the AI request.** `ai_usage.add` queues the request and a short-lived background thread writes it, waiting up to 30 seconds for a busy database (a CSV import asks for categories before it commits its own transaction) and retrying up to 20 times, 3 seconds apart. Anything still queued is written when the next request is made or Admin → AI usage is opened; the queue is capped at the latest 1,000. A failure is logged at info level and never raised.
- **Older databases.** Migration 0024 starts the log from what the database already holds: each statement's, utility bill's, toll statement's and CSV import's stored `ai_usage` (its last run, dated by `processed_at`, else `uploaded_at`; `imported_at` for CSV imports), marked `earlier = 1`. Anything there that isn't a list of objects (bad JSON, a stray value, non-numeric token counts) is skipped or left blank, never an error, and the copy runs only once. The same happens to an imported backup from an older version, since importing runs the migrations (section 3).
- **The page:** **All time** (requests with how many failed, input, output and total tokens, time the AI took, cost) with the dates covered; then **By month** (UTC, newest first), **By kind of request** (Extraction attempts counted together; Categorization, Warm-up, Debug re-send, Hi / Write SQL / Fix SQL) and **By model**, each with the same columns. When some of the total came from documents already in the app, the page says how many requests and tokens, and that earlier re-runs and anything already deleted weren't counted. Cost uses today's prices on App settings ("—" with both prices 0). With no requests yet it says so; if the database has no `ai_usage_log` (an imported backup whose migrations failed) it asks for a restart.
- It doesn't depend on Show AI token usage, which is only for the debug pages. There is no reset.

## 22. AI providers (`app/ai_client.py`)

### 22.1 Choosing one

- On Admin → App settings → AI: **Provider** — Ollama (on your network, the default), OpenAI-compatible (OpenAI, OpenRouter, Groq, LM Studio, vLLM, Ollama's own `/v1`, …) or Anthropic Claude. **Address** — Ollama's is required; blank means `https://api.openai.com/v1` or `https://api.anthropic.com`. **Model** — reads the PDFs, so it must accept images. **Access key** — required for Claude, optional for OpenAI-compatible (local servers need none), unused by Ollama. **Text model (optional)** — used instead of the model for categorization and Write SQL. **Longest answer (tokens)** — Claude's `max_tokens` for a PDF (default 16,000).
- The requests, retries, the 400 fall-backs, error mapping and model listing are
  the shared `app/common/ai_client.py` (`Client`, `Wording`); Finance's
  `app/ai_client.py` keeps `Config`, `current()`, `generate()`, its usage notes
  and its wording.
- Every AI use goes through one client: statement, utility-bill and toll extraction, categorization, Write SQL and the debug re-sends. The provider and key are read at each request, so a change applies to the next one.
- **Requests.** Ollama: `POST {address}/api/generate` (images as base64, `format: json`). OpenAI-compatible: `POST {address}/chat/completions` with `Authorization: Bearer <key>` when a key is set, the prompt and page images (PNG data URLs) in one user message, `response_format: json_object` — retried once without it (or with `max_completion_tokens`) if the server rejects that. Claude: `POST {address}/v1/messages` with `x-api-key` and `anthropic-version: 2023-06-01`, images as base64 blocks before the prompt, `max_tokens` from the setting — lowered to the limit the model names (else 8,192) if the model allows less.
- **Warm-up and "hi" are Ollama-only**: a cloud provider has no model to load, and a "hi" would only cost tokens.
- **Errors say what to do.** 401/403: the key was refused (check it on App settings). 429, 500, 502, 503, 504, 529: retried up to 3 times, waiting `Retry-After` (capped at 60 s) or 5, 15, 45 s; then "rate-limiting" / "overloaded, try again later". 404: check the address and model. Unreachable and timeouts name the provider and address. The provider's own error message is included; the key never is.
- **Test connection** asks the provider for its model list (`GET /api/tags`, `GET {address}/models`, `GET /v1/models`) with what is typed (a blank key field uses the saved key): nothing is generated, so it costs nothing and never disturbs a PDF. It says whether the model is offered and lists up to 40 models.
- Jobs still run one at a time behind the same lock, whatever the provider.

### 22.2 The access key

- Stored in `app_settings` (`ai_api_key`). The page never shows it: the box is empty, with "A key is saved (…last 4)"; a blank box keeps the saved key; **Remove the saved key** clears it.
- Only ever sent in a request header, never in a URL, a log line, an error message or a page. `app_settings` is hidden from the Query tab.
- **Database download** (Admin → Storage) blanks it in the copy. **Import** of a backup keeps the key this install already has.

### 22.3 Home network or not

- The AI counts as outside the home network when its address is a public host: not a private, loopback or link-local IP, not `localhost` or a single-label name, not `.local`, `.lan`, `.home`, `.internal`, `.localdomain`, `.home.arpa` (nor the reserved `.localhost`, `.test`, `.invalid`).

### 22.4 Privacy notice

- While the AI is outside the home network, **Home**, **App settings** and the **Statements, Utility bill and Tolls upload pages** show, to everyone: "Your documents go to <provider>" — PDFs are sent as page images to <host>, transaction descriptions for categories (and the admin's Write SQL sends table names, accounts and categories), and that service's own privacy and retention rules apply. The admin gets a link to App settings.

## 23. Optional parts: Utilities and Tolls

- **App settings → Features:** Utilities and tolls, each on or off. A new install starts with both off; an upgrade that already has utility bills (or toll statements) keeps that part on (migration 0023).
- **Off** hides it everywhere — the Bills nav item (when both are off), the Upload tabs Utility bill / toll statement, the Bills tabs — and every one of its pages and actions answers 404 "<part> is turned off" (the admin gets a link to App settings). Nothing is deleted; switching it back on shows everything again, without a restart. `request.state.features` carries the switches to every page (`app/features.py`).
- **Other utility providers.** Xcel Energy and Aurora Water keep their tuned prompt and the check that the PDF names the provider. **Other provider…** on the upload form takes a name (up to 60 characters; a typed known name maps to it): the bill is read with a general electric / gas / water prompt naming that provider and **always waits for review** ("isn't one of the providers the app reads by itself…"). The person checks the values against the PDF, corrects them and Confirms, or **Re-extracts** with notes.
- **Re-extract a utility bill** (the review page, any provider, while the PDF is kept and the bill isn't confirmed): notes on what was wrong (up to 2,000 characters) are saved on the upload's first row (`utility_bills.extraction_notes`) and sent with the PDF; the whole upload is read again (Xcel's gas with its electric) and returns to Upload → Utility bill. The debug re-send uses the same prompt and notes.
- Other providers appear in the Dashboard's provider filter once they have bills.

## 23a. The household apps bus and the Household Assistant (`app/app_messages.py`, `app/tools.py`)

- The shared `app_bus.py` (with `ha_ws.py`) starts in the lifespan on the app's own database (`db.get_db`); it makes
  its tables (`bus_outbox`, `bus_seen`, `bus_apps`) itself, and `query_engine.HIDDEN_TABLES` keeps them out of
  Query and Reports. Its own WebSocket; the outbox thread only with a Supervisor token. The tools only read, inside
  the bus's transaction.
- It answers the **Household Assistant** (`assist.tools.list`, `assist.tool.call`; the shared `assist_tools.py`,
  APP_MESSAGES_SPEC §6.6). `requested_by` must be in `known_users` (has opened the app), else `nack not_allowed
  no_access`. Whose data: theirs, or with `person` (a name or id) an owner `user_access` lets them see; anyone else
  is `nack not_found person`. The admin's "act as" never applies.
  - `finance.summary` (`month?`): income, spending, net and the top 5 categories, with the Overview's rules (clean
    rows; no transfers or excluded rows; income only on checking/savings; a card purchase is spend).
  - `finance.spending` (`month?`, `category?`): spend by category, or a category's 10 largest charges as
    `{date, merchant (the bank's description), amount, account}` — **never `note`**.
  - `finance.recurring`: `recurring.scan_recurring`'s recurring series as `{merchant, amount, frequency, next, last,
    category, account}`; `finance.bills` (`days?` 1–60, default 14): those whose next date falls in the window.
- **Off by default** (App settings → Household Assistant → `assistant_answers`, `nack not_allowed off`): money is
  private. Each person's own *Let the Household Assistant answer for me* (`known_users.assistant_ok`, migration 0025,
  on by default): a card on Who am I while the admin's switch is on; POST `whoami/assistant` (`assistant_ok` 1/0,
  always the signed-in person's own, never the "view as" person's; 303 back); off → `nack not_allowed person_off`.
  Links: the sidebar page from `assist_tools.sidebar_page`,
  with `/month/<YYYY-MM>` (summary, spending) or `/recurring` (recurring, bills).
- **Sub-path links**: `/<page>/month/<YYYY-MM>`, `/<page>/dashboard`, `/<page>/recurring`. Every page carries the
  sidebar page in `<body data-page>` (set by the `_features` middleware); `static/base.js` hands it to the shared
  `common/static/deeplink.js`, which reads the rest of the path and goes to `dashboard?period=<month>`, `dashboard` or
  `recurring`. A request for `/month/<YYYY-MM>` reaching the app itself is redirected to `../dashboard?period=…`.

## 24. Packaging for other installs

- **App repository:** `repository.yaml` at the root (name, url `https://github.com/sameerkotra/ha-apps`, maintainer), shared with the other household apps; this app in `finance/`. Home Assistant builds the image on install from the `Dockerfile` (multi-arch Python base; amd64 and aarch64); no prebuilt images.
- **64-bit only:** `arch` is `amd64` and `aarch64` (the `python:3.13-slim-trixie` base); README.md and DOCS.md say so, and the app isn't offered on 32-bit systems (armv7, armhf, i386).
- **Store and Documentation tabs:** `finance/README.md` (short description) and `finance/DOCS.md` (setup and everything the app does, for users). `icon.png` (128×128) and `logo.png` (250×100) are the app's own artwork. `translations/en.yaml` names and explains the two options. `CHANGELOG.md` (shown in Home Assistant's update dialog) starts at the release version, newest first: its top `## <version>` section equals `config.yaml`'s version (a test checks); each later release adds its section above.
- **Options:** only `admin_users` and `trusted_client_ips`, both empty by default; everything else is on Admin → App settings. Nothing in the shipped files names the author's network or accounts (a test checks the tracked files).
- **First run:** while `admin_users` is empty, every page shows **No admin yet** with the viewer's Home Assistant user name, saying to add it to `admin_users` on the app's Configuration tab, save, and restart the app, then set up the AI on App settings (section 3; the restart path is on How the app sees you). Nobody is admin until then.
- New installs start with Utilities and Tolls off and no AI configured (section 23, 22).

## 25. Layout, status and limitations

### 25.1 Layout

```
finance/                    the app (slug "finance")
  config.yaml  Dockerfile  bootstrap.py  requirements.txt  requirements-dev.txt  DEV.md
  README.md  DOCS.md  CHANGELOG.md  icon.png  logo.png  translations/en.yaml  .dockerignore  pytest.ini
  spec/         SPEC.md (this file)  finance_schema.sql
  app/          FastAPI app
    main.py security.py auth.py db.py storage.py jobs.py purge.py
    settings.py ai_client.py ai_usage.py features.py ...
    common/     shared Python (copies of the repository's common/): whoami, auth_core,
                db_core, web_security, backup_core, ai_client, csv_export
    parser/     text readers, vision readers, the three pipelines, shared row helpers
    routes/     one module per area (accounts, transactions, csv_import, review,
                upload, admin_storage, ai_usage_page, settings_page, utilities, tolls, ...)
    templates/  static/  (style.css, ui.js, base.js, nav-boot.js, query.js, htmx.min.js, pages/*.js;
                static/common/: theme-boot.js, themes.css, ui.js — shared copies)
  migrations/   0001-0025, applied at startup (and after a database import)
  tests/        pytest suite (python3 -m pytest); tests/common_tests/: shared helpers and tests (copies)
```
`app/common/`, `app/static/common/` and `tests/common_tests/` are written by
`tools/sync_common.py` from `common/manifest.json` (see `common/README.md`); never
edit a copy (`tests/common_tests/test_shared_copies.py` fails if one was changed).
Finance gets none of `ha_client`, `ha_time`, `housekeeping`, `settings_core`,
`people_admin` or the shared settings/people pages: its time zone comes from
`bootstrap.py` (`TZ`), and its App settings page and Users tab are its own.

### 25.2 Verified

- About 180 automated tests pass (`finance/tests`): every page renders empty
  and with data; the statement pipeline (zero-amount lines, duplicates,
  re-extract, restart, interrupted jobs); CSV import rules; review filtering;
  toll statements end to end; utility bills from a provider the app doesn't know
  (confirm and re-extract with notes); security (ingress-only, cross-site POSTs,
  upload limits, stored-path confinement, backup/restore, purge); Query tab and
  Reports; shared access; App settings (validation, no restart, the access key
  never shown or downloaded); the three AI providers' requests, usage, errors
  and retries (faked); the privacy notice; Features on and off; the packaging
  files (changelog, 64-bit notice) and first-run notice.
- Real-world with Ollama: statements from the household's banks and cards, Xcel
  dual-fuel and Aurora Water bills, and real toll statements. The
  OpenAI-compatible and Claude providers are tested against faked responses
  only so far: try one statement with each before relying on it.

### 25.3 Known limitations and things to watch

- The vision model is not deterministic: a statement can come back wrong once
  and right on **Re-extract**.
- Password-protected PDFs and scanned (image-only) PDFs are not supported.
- The confirm page has no "numbers don't match" outcome; fix rows through
  Transactions or Review.
- Recurring detection tolerances (`app/recurring.py`) and toll-trip constants
  (`app/toll_trips.py`) are tuned on limited real data; adjust there if a
  merchant or a trip is grouped wrongly.
- Ollama traffic is plain HTTP on the LAN.

## 26. Build, test and release

- **Tests:** `cd finance && python3 -m pip install -r requirements-dev.txt` once, then
  `python3 -m pytest`. Every change gets a test. `tests/common_tests/` holds the shared
  pieces (copies): `packaging_core.py` (the packaging checks `test_packaging.py` runs
  with Finance's values), `test_shared_copies.py` and the shared modules' tests
  (`test_whoami_shared`, `test_auth_core`, `test_db_core`, `test_web_security`,
  `test_backup_core`, `test_ai_client`, `test_csv_export`).
- **Version:** bump `version` in `config.yaml` for every release, or Supervisor won't offer
  the update.
- **Local install:** copy this folder to `/addons/finance` on the Home Assistant host, then
  Settings → Apps → Install app → ⋮ → Check for updates; it appears under Local apps.
- **Public release** (GitHub `sameerkotra/ha-apps`, the repository shared with the other
  household apps): commit this folder's changes there (not `__pycache__`, `.pytest_cache` or
  local databases) with a short release message and push. The repository root holds
  `repository.yaml` (url `https://github.com/sameerkotra/ha-apps`, maintainer `sameerkotra`), `README.md`
  and `LICENSE` (MIT).

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD: here `bootstrap.py`, as before), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: `security.py` runs the shared `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets a plain-text 403 "Forbidden: cross-site request" (as before); `same-origin`, `none` and a missing header pass.
- **Backups without secrets**: the download blanks the secret App settings (`ai_api_key`, stored as raw text: blank is `''`) in the copy (`backup_core.blank_settings`); a restore keeps this install's value for each one the file leaves blank (`backup_core.saved_settings` before the swap, `keep_settings` after the migrations, `updated_by` 'kept on import'; a value the file carries is used).
- **PDF tools** (`pdftotext` in `parser/deterministic.py` and `pdfview.py`, `pdftoppm` in `parser/vision.py` and `pdfview.py`) run through `app/common/sandbox_run.py`: on a copy in a scratch folder, as the unprivileged `pdfworker` user made in the Dockerfile (no supplementary groups), with limits (1 GiB address space, 120 s CPU, 512 MB files, 64 open files, 64 processes, no core dumps) and the same timeouts as before; `lock_down` closes the data folder to other users at start-up. Without root (tests) only the limits apply.
- `config.yaml` spells out `apparmor: true`, `hassio_api`, `auth_api`, `docker_api`, `full_access: false`.
