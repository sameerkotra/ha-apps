# Changelog

## 1.7.2

- **Back goes to the page you came from.** The phone's back gesture (and the browser's Back) now steps back through the pages you opened in the app, instead of jumping straight to the start page; dialogs still close first, and Back on the start page still leaves the app.

## 1.7.1

- **Fixes to one person's data**: The Data dialogs, after a check in the browser: the ⋯ Data menu stays on screen (it opens the other way near an edge or a bottom bar), a wrong name is caught before the copy is downloaded, the copy uses the export's usual choices, the red button is readable in every theme, and the name box only shows when it's needed. On a phone, the last person's menu opens clear of the bottom bar, and the import preview says "1 folder", not "1 folders".

## 1.7.0

- **One person's data: export, import, delete.** Admin → People has **⋯ Data** on each person: **Export their data**
  (their folder with History and, if you like, their Trash, their templates, tags, favourites, saved searches and
  settings, as one .zip — big folders are prepared in the background), **Import into this person** (or **Import a
  person's data…** above the list: add to what they have in a folder of its own, skipping what's already there, or
  replace it) and **Delete their data…**.
- **Delete their documents is now part of ⋯ Data → Delete their data…**, with three choices for their folder:
  **Move to Trash** (as before, the default), **Give to someone** (everything moves into that person's folder as
  "From …", with its History) or **Delete for good** — and then keep them, empty, or forget them. What they made in
  other people's folders stays; once they're forgotten it shows as made by "Former member".

## 1.6.4

- **Sheets: reorder tabs by dragging.** Drag a tab to where you want it (on a phone, hold it for a moment, then drag). The tab menu also has **Move to start** and **Move to end**, and on a phone holding a tab and letting go opens that menu.

## 1.6.3

- **Sheets: dates that look like dates.** `=TODAY()`, `=DATE(…)`, `=EOMONTH(…)` and those plus a number of days now show as a date instead of a number like 46312 (and are saved as dates, so they open as dates in Excel). A date minus a date stays a number of days.
- **Sheets: dates with the month's name.** `Aug 8 2026`, `August 8, 2026`, `8 Aug 2026` and `8-Aug-2026` are dates when typed, and dates already in a cell as text like that now work in `DAYS` and date sums (they gave `#VALUE!`).

## 1.6.2

- The image is built on one fixed Alpine release, and no longer installs python-multipart, which the app never used.
- **No more freezes from a name pattern**: a filing rule or a search with many `*` (like `*a*a*a*a*b`) could stop the app for minutes, and after a restart again. Patterns are now matched in one pass, and Home Assistant restarts the app if it ever stops answering (a watchdog).
- **Download as PDF of an odd note** (a very long word, a line of `#`s or `|`s, lots of `*` or `[`) took minutes and held the app; it now takes a moment. The PDF is made in a separate process that stops after 60 seconds — a note too long for that says to use Print → Save as PDF.
- **Opening a folder no longer stalls everyone else's saves**: looking for changes on disk read and hashed the files while holding the database; it now reads first and writes in small steps. If the database is busy anyway, the app says "try again in a moment" instead of failing.
- **Sheets that wouldn't open**: a formula nested hundreds of brackets deep made the sheet impossible to open for everyone. Formulas are limited to 64 levels (as in Excel), such a formula isn't saved, and a broken one only shows an error in its own cell.
- **Sheets that froze the page**: COUNTIF, SUMIF and the other *IF(S) functions over a huge range (A1:XFD1048576), wildcard criteria like `"*a*a*a*b"`, NETWORKDAYS over long spans and MAX/MIN over 150 000 numbers are now quick (or give #NUM! when a sheet asks for far too much at once).
- **Notes' preview stays quick** whatever is pasted: emphasis, links and headings no longer take longer and longer on long lines, a list item with many lines is read once, and a paragraph over 20 000 characters is shown as plain text. The preview of a long note waits a little longer after you type.
- **Nothing is lost when a save fails before you leave**: going to another page waits for the save; if it can't be made (offline, a conflict, an error) you're asked "Leave without saving?", and the text is kept on this device and offered again ("Put them back") the next time you open the note here. Closing or reloading the page with unsaved changes asks first, and a big note is saved sooner after each change.
- **Two people ticking the same checklist at the same moment**: one of them could lose "ticked by", and History showed a version by nobody. Saves of notes, checklists and sheets now finish completely before the next one starts.
- **A sheet cell edited while it was being saved** no longer reports a conflict with yourself.
- **A checklist moved to Todo in several parts** could leave some of its items behind in Docs (the second part's answer arrived while the first part's items were being taken out).
- Text with control characters (pasted from elsewhere, or in a .csv) no longer stops a sheet saving as .xlsx — they're taken out.
- **Google Keep import**: a note with a very long title no longer stops the whole import (titles are cut at 120 characters; a note that can't be imported is skipped). A .zip whose folders would go past 10 deep in the folder you chose is refused before anything is made, and its folders that can't be made are skipped with their files.
- **Kids' space**: a child's Keep import no longer gets Keep's old dates, which hid those notes from their parents' view; search no longer shows a child cells of a sheet (only that the sheet matched), and a child can't pin a sheet's cell or show it in Home Assistant.
- **Security — Home Assistant sensors**: in a person's folder only its owner or a manager can now show an item in Home Assistant (everyone who uses Home Assistant sees it); editors can still turn a sensor off.
- **Security — uploads** are put in place relative to their folder, opened and checked, so a folder swapped for a link at the last moment can't send the file elsewhere.
- **Security — backups**: the database in a backup no longer holds the documents' text (the search index's words, PDF text and, unless the files are in the backup, text the AI read). After a restore the app reads the files again. The backup is now written while it downloads, not first copied in full into the app's storage, and leftover copies from a stopped download are removed after 6 hours.
- **Security — AI**: people see only the AI's host (never a user name, password or `?key=` in its address); an AI answer's links are shown as text with their whole address, not as links. Regular-expression search has one 5-second limit for the whole search and its worker limits its own memory and CPU. The log no longer names files when the file system refuses something.
- **Disk and AI limits by default (App settings)**: *Keep this much disk space free* (new, 200 MB) stops saving, uploading, copying and importing before Home Assistant's disk is full; *Limit per person for their own folder* is now 100 GB by default (it was none) and counts the versions kept; *Most tokens a month* is 20 million by default (it was none) and the new *Most tokens a month per person* 5 million. An admin can change each, or set 0 for no limit, as before. A copy is at most 1 GB, a scan 30 pages.
- **Faster**: the storage report's duplicates, follow notifications (most minutes nothing new happened), search results (snippets and "open at line" only for the page shown), big sheets (scrolling, moving and resizing no longer re-read every cell; colour rules on whole columns no longer read every row; undo keeps at most 20 whole-sheet copies), the scan's straightening, and folder queries. The page's scripts and styles are kept by the browser until the next version.
- **Easier with a keyboard and screen reader**: menus move with ↑ ↓ Home End and give the focus back when they close; the sheet grid names its cells and the active one; keyboard focus is always visible; the "select all" box in search results can be reached; the flash on a found line stays still when you ask for less motion. A dragged scan corner or column width that the browser cancels goes back.
- **Shared code**: App settings has a new *App-messages signing key* (Household apps): with the same key in every household app their messages to each other are signed and unsigned ones refused (off until you set one). Answers from `/api/` are never cached, and the browser keeps each version's scripts and styles instead of fetching them again. Request size limits also count bodies sent without a length.

## 1.6.1

- **The PDF viewer on phones**: closing it now lets go of every drawn page at once (they used to stay in memory until the next PDF), and zooming quickly no longer draws the same page twice.
- **A PDF whose first page is broken** now says it can't be shown here, with the Download link, instead of loading for ever.
- **Kids' space**: a child can no longer download a sheet shared with them as a file — not the sheet, not a kept version of it — and a `.zip` they download leaves sheets out.
- A picture in a Markdown note that can't be shown (deleted, or not shared with you) turns into its name more reliably.
- **Shared code**: the Household Assistant tools answer only calls that come from the Household Assistant itself; AI requests never follow a redirect; pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 1.6.0

- **View PDFs in Docs**: **👁 View** in a PDF's file panel (or ⋯ → **View**) shows it inside the app, full screen, page by page — zoom (− / Fit / +), the page number, Download at hand, on phones too. A password-protected PDF asks for its password. The app draws the pages itself, so nothing in a PDF runs (its links and forms don't work in this view); PDFs over 50 MB still download.

## 1.5.0

- **Pictures in notes**: paste a picture into a Markdown note (or use 🖼 on its toolbar — on a phone that can be the camera) and it's saved next to the note and shown in the preview. Only pictures kept in Docs are ever shown; links to pictures elsewhere still aren't loaded.
- **Formatting on phones**: the Markdown toolbar sits under the text, in reach above the keyboard, with numbered lists, quotes, code, document links and pictures added, and tapping it no longer closes the keyboard.
- **View links**: Share → **Make a view link** gives an address anyone in the household can open to view it (a new address or **Turn off** whenever you like; turning it off removes the access it gave).
- **Sheets**: more number formats — Accounting (negatives in brackets), Scientific, Time and Date and time; typing "17:30" or "5:30 pm" makes a time. A ▾ on each column header opens its menu (sort, filter, total …) without right-clicking.

## 1.4.0

- **Full screen**: ⛶ next to Share in every note, checklist and sheet gives the document the whole screen — the sidebar, search bar and bottom bar step aside (and the browser goes full screen where it's allowed). ⛶ again or **Esc** brings them back.
- **Sheets**: a sheet now shows 1 000 rows and columns A–Z from the start, and grows as you move past them (up to 5 000 rows and 100 columns). Before, an empty sheet stopped at row 60 and column P.
- **Sheets**: the word "null" no longer shows under the last row.

## 1.3.0

- **Checklists from the Household Assistant**: "Tick passports on the packing list" or "Add snacks to it" — only after you tap the exact change, and only where you could do it here (a viewer ticks only when the checklist lets viewers tick).

## 1.2.4

- **Home Assistant's time zone, always**: when the app started before Home Assistant was answering (after a reboot), it stayed on UTC — so in the Americas "today" turned into tomorrow in the evening. Now it uses the zone the Supervisor gives every app (Home Assistant's own) until Home Assistant answers, keeps asking until it does, checks again every six hours (a changed zone needs no restart), and the whole app follows that zone.
- The Household Assistant gets a document's modified date in Home Assistant's zone (it was UTC's).
- **Steady connection to Home Assistant's events**: the log showed "Home Assistant event connection: TimeoutError" and a reconnect every minute. Answering the Supervisor's keep-alive ping left the app waiting for a message that didn't come, so its own keep-alive stopped and the read timed out; for a few seconds each minute, messages between the household apps could be missed. It now answers and carries on.

## 1.2.3

- **The Household Assistant finds your sheets**: asked about "the budget sheet", it found nothing, because the
  word "sheet" isn't in the sheet itself. Words that only say what kind of thing it is ("sheet", "note", "list",
  "file") are now left out of the search, "sheets" on its own lists your sheets (uploaded spreadsheets too), and
  when nothing has every word, what has any of them is given.

## 1.2.2

- **Shared AI code**: the AI connection can offer tools to a model in its own way (used by the Household Assistant); nothing changes in this app.

## 1.2.1

- **Sheets keep their formula results in .xlsx**: a saved .xlsx held each formula but not its result, so Excel and other apps (and Docs' own Home Assistant sensor for a cell) saw an empty value until the sheet was recalculated. The results are written again.

## 1.2.0

- **Answers the Household Assistant**: the new Household Assistant app can search Docs ("Find the note about the boiler"), read a note, checklist or sheet (4,000 characters at a time), show a checklist's open items, and — only after the person taps the exact note it proposes — make a note in My docs → Inbox. Always as that person, from what they could open here; Kids' space rules hold. Each answer links back to the document. On by default; an admin can turn it off in **App settings → Household Assistant**, and each person in **Settings → You**.

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
