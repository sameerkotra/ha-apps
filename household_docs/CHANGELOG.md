# Changelog

## 1.1.3

- **More room to edit on a phone**: Share and the ⋯ menu now sit on the same line as the document's name (a long name is cut with "…"; tap it to rename), with the save state and "changed … by" in small print underneath. Notes, checklists and sheets get up to about 100 px more editing space on a phone.

## 1.1.2

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 1.1.1

- **Sheets**: the cell (or range) the arrow keys pick while you type a formula is now outlined on the grid, so you can see what you're pointing at.

## 1.1.0

- **Notifications open the app again**: tapping a Docs notification on a phone opened a "404: Not Found" page in current Home Assistant. They now open the app's sidebar page (or its Settings → Apps page when it has no sidebar entry). The dashboard address for a quick note is `/<full slug>/quick-note` too — see DOCS.md.
- **Sheets**: right after `=`, an operator, `(` or `,` in a formula, the arrow keys pick a cell and put its address in (Shift for a range), as in Excel and Google Sheets.
- **One Activity page** with tabs **Recent · Activity · Trash · Storage** instead of four sidebar entries; **Home** shows pins, favourites and My docs (no Recent list any more).
- **Everyone** has its own icon (👪) so it no longer looks like Home.
- **Send to Todo** no longer says "This checklist has no items" for a checklist that has them: the dialog reads the checklist fresh instead of an old copy.

## 1.0.1

- **Security**: backups no longer include the AI access key, and restoring a backup keeps the key this install already has. After restoring on a new install, enter the key again in Admin → App settings.
- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 1.0.0

- **First release**, published in the household apps repository (https://github.com/sameerkotra/ha-apps).
- **Notes and checklists** as plain files in Home Assistant's `/share`: notes are `.txt`, checklists are Markdown
  task lists (`.md`) that open in any editor. Autosave, Wrap and Monospace for notes; tick, edit, indent, reorder,
  Hide ticked and Untick all for checklists, with who ticked what and when.
- **Sheets** — spreadsheets with formulas, saved as `.xlsx` (or `.csv`) that open in Excel and LibreOffice with the
  numbers worked out: number formats (currency from Home Assistant), bold, alignment, red negatives, widths,
  frozen rows and columns, a totals row, sort, filter, fill down / right, copy and paste with Excel and Google
  Sheets, insert and delete rows and columns, 40-odd functions (lookups, conditional sums, dates, PMT …) with a
  function list and suggestions, tabs with references between them, charts (bar, line, pie) and conditional
  colours, undo, printing, export (.xlsx, CSV) and import. Several people can edit one sheet: only the same cell
  conflicts. Excel files from elsewhere open read only when they hold things the app can't keep (macros, pivot
  tables, pictures, comments, merged cells …), with *Save a copy to edit*; untrusted files are read in a separate,
  time-limited process. Search finds text in every cell and opens the sheet at it.
- **Folders** in your own **My docs**, plus **Favourites**, **Recent** and **Trash** (restore, or emptied after
  30 days by default).
- **Sharing** with chosen people or **Everyone** — Can view, Can edit or Manager; folder shares reach everything
  inside, also files added outside the app. **Shared with me** and **Everyone** pages, Leave and Hide, Transfer
  ownership, and *Viewers may tick items* per share. A phone notification when something is shared with you
  (each person can turn it off in Settings).
- **Safe saving**: write-to-temp-then-rename, conflicts answered with *Keep mine / Use theirs / Compare*, changes
  made outside the app noticed by a scan and whenever something is opened, and **History** with earlier versions.
- **Uploads, previews and downloads**: upload any file (button, ➕ New or drag from your computer) with Replace /
  Keep both, picture previews (checked to really be pictures), folders and selections as `.zip`, and earlier copies
  of replaced files for 7 days.
- **Shared folders**: an admin opens existing `/share` folders to chosen people (or Everyone), read only or read
  and write, with checks that keep the documents folder and Household Chat's files out of reach.
- **Several at once**: tick boxes with Move, Copy, Share, Download and Delete, drag and drop onto folders, and a
  grid view with thumbnails.
- **Markdown notes** (`.md`) with Edit / Preview / Split, a small toolbar and tick boxes you can tick in the preview
  (nothing in a note is ever run: HTML shows as text, only web links are made, pictures aren't loaded); *Make it
  Markdown / plain text*. **Find & Replace** in notes, Find in checklists and sheets, tappable web links.
- **Links between documents**: type `[[` to link a document or folder; links survive renames and moves, and each
  document shows *Linked from* (only what you can open; "🔒 No access" and "Deleted" for the rest).
- **Tags and colours** on any file or folder, a 🏷 Tags list in the sidebar, *Tag…* for several items at once, and
  `tag:` / `-tag:` / `color:` and Tag and Colour filters in search.
- **Pins and Quick note**: up to 8 pinned documents and folders on the home page with previews (a checklist's
  progress, a sheet's chosen cell), dragged into order; **⚡ Quick note** (also `N`, or a dashboard button) makes a
  note in Inbox, named after its first line when you leave it.
- **Print and PDF** for notes, Markdown notes and checklists: a print-only page, *Download as PDF* (made in the app)
  and *Save PDF here*.
- **Moving the documents to a new location**: read-only mode for everyone while you copy, the exact copy commands,
  a copy check (missing, different, extra; quick or with SHA-256) with a full report, then a switch that keeps every
  share, tag, link and earlier version — and *Switch back* while the old folder is there.
- Phones: swipe a checklist item to tick it. A personal **number style for sheets** (1,234.56 · 1.234,56 ·
  12,34,567.89) and default note type.
- **Search page**: everything you can open by name (contains, starts, exact, ends, wildcards, regular expressions)
  and by text (words, phrases, OR, leaving words out, patterns), with filters for dates, sizes, types, people,
  sharing and more, chips, sorting, grouping, a CSV of the results, and saved (pinned) and recent searches.
- **Admin**: People (access, folder name, counts and sizes, phones and notify services, delete someone's
  documents), **Shared folders**, **Documents folder** (first-run choice with a folder browser over `/share`,
  checks, Rescan now), **App settings**, and **Backup** (the database, optionally with the documents; its size
  shown first) and restore.
- **🕑 Activity**: what changed in everything you can open (added, edited — grouped —, renamed, moved, deleted,
  shared with you, and changes made outside the app), with filters. **Follow** a document, folder or shared folder
  for phone notifications: at most one per item every 15 minutes, never for your own changes, with quiet hours.
- **Home Assistant sensors**: a checklist's open items, a sheet's cell or range, or a folder's file count on your
  dashboard (⋯ → Show in Home Assistant), updated on every change; everyone who can see the item is told.
- **💾 Storage**: size, types, growth over 12 months, largest and old files, empty folders and duplicates (*Keep
  this one*) for your folder and your shared folders; Admin → Storage with every folder's totals, History, Trash,
  `/data` and *Empty all Trash now*.
- **PDF text search** (read in a separate, time-limited process; password-protected and scanned PDFs are marked),
  with the page in the results.
- **📷 Scan** with the phone's camera: several pages, corner handles to crop and straighten, black and white, saved
  as one PDF (made in your browser) or JPEG pictures, named by your own pattern.
- **Imports**: Google Keep notes from a Google Takeout `.zip` (lists become checklists; labels, colours, pins and
  archived notes come along; importing again adds only what's new), and any `.zip` into a new folder, with the
  unsafe parts left out.
- **Optional AI** (off until an admin sets it up — Ollama, an OpenAI-compatible service or Anthropic Claude): read
  text in pictures and scanned PDFs (also automatically per folder), summaries, checklists from text, sheets from a
  table or a photo of one, and questions about a folder that cite the files used. Every action names the provider
  and what it sends before anything leaves; Admin → AI usage with a monthly limit.
- **Send to chat** (with Household Chat 2.2.0): ⋯ → Send to chat… posts a card in a chat you pick — title, kind and
  whose it is, never the content — with *Open in Docs*; optionally gives the chat's members you tick access, only
  after Chat posted it. Links to something you can't open show a "🔒 No access" page that names nothing.
- **Checklists to Todo** (with Household Todo 2.3.0): ⋯ → Make a Todo list… — a new list or one you have, the items
  you tick (done ones only if you include them), *Keep* or *Move* (items leave only once Todo has them), long
  checklists in parts; a note on the checklist says where they went. **Admin → App settings → Connected apps.**
- **Templates**: meeting notes, home maintenance log, monthly budget, emergency contacts, babysitter info and trip
  plan built in; ⋯ → Save as template for yourself or (admins) the household.
- **Filing rules** per folder for files that arrive (uploads, scans, files added outside the app): name patterns,
  type and size, rename with {date} {yyyy} {mm} {dd} {name} {n}, move; first match wins, a dry run, and Undo for 7
  days from Activity; with AI, a suggested name and folder. **Clean-up rules**: to Trash or an Archive folder after N
  days, favourites and pins left alone.
- **Kids' space**: child accounts with notes and checklists only — no sheets, sharing, Everyone, shared folders
  (except Kids folders), AI or pattern search, enforced by the app; parents an admin chooses (never themselves) can
  view what a child makes from then on, and the child is told who.
- **What changed since you last looked**: dots in lists and search, `is:new`, the changed lines of a note (and
  checklist items, sheet cells) when you open it, and Mark all as seen.
- Phone layout with a bottom bar; Midnight, Slate, Daylight and Auto themes; the back gesture closes what's open
  before leaving the app.
