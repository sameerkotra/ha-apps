# Household Docs

Household Docs keeps the household's everyday documents — notes, checklists, sheets, and any file you upload — in
folders, lets you share them with chosen people or with everyone, opens existing `/share` folders to the people an
admin chooses, and searches everything you can open. Everyone opens it from the Home Assistant sidebar (**Docs**) and is
recognised by their Home Assistant account; there is no separate login.

**Every document is a plain file in Home Assistant's `/share` folder.** Each person's documents are in a folder
of their own (`/share/household_docs/people/<name>/` by default), so they open over Samba, in the File editor or
in any text editor too. The app's own database (in the app's `/data`) holds only what files can't: who may see
what, the search index, checklist tick details, favourites and settings. Home Assistant's backups include the
documents when **Share** is ticked; the app's own backup holds the database (and, if you choose, the files).

> **Docs are plain files, readable by anyone with access to `/share`.** Samba or NFS users, the File editor,
> other apps with access to Share (Household Chat, Family Tree, …) and anyone holding a Home Assistant backup that
> includes Share can read and change *everyone's* documents there. The sharing you set in the app applies inside
> the app only. Keep passwords and card numbers in Household Vault — the editor reminds you, and warns when
> something looks like one.

## Getting started

1. In Home Assistant go to **Settings → Apps → Install app**, open the **⋮** menu → **Repositories**, add
   `https://github.com/sameerkotra/ha-apps`, then install **Household Docs**, start it and open it from the
   sidebar (**Docs**).
2. Add your Home Assistant user name to `admin_users` on the app's Configuration tab, save and restart the app —
   until then every page shows "No admin yet" with the name to add.
3. As an admin, the home page asks **Where should documents be kept?** with the default
   (`/share/household_docs`) filled in. **Use this folder** confirms it; **Browse…** picks another folder inside
   `/share` (network storage mounted there works too). People can use the app straight away with the default.
4. Everyone else just opens **Docs**: their own folder is made the first time they do.

**How the app sees you** (Settings, or your name at the bottom of the sidebar) shows who the app thinks you are
and why you are (or aren't) an admin.

## Spaces

The sidebar (on a phone: the bar at the bottom — **Docs · Search · ➕ · Shared · More**) has:

- **Home** — your **📌 pinned** documents and folders, favourites and the top of My docs.
- **📄 My docs** — your folder, with folders inside it as deep as you like (ten levels).
- **👥 Shared with me** — what others shared with you, each with its owner. **Show hidden** brings back what you hid.
- **👪 Everyone** — what people shared with Everyone (your own too).
- **📁 Shared folders** — folders in `/share` an admin opened up for you (shown when you have any), each 🔒 read
  only or ✏️ read and write.
- **⭐ Favourites**, **🏷 Tags** (your most used tags, with counts — shown once there are tags) and
  **🔎 Saved searches** (pinned ones are listed under it).
- **🕑 Activity** — one page with tabs: **Recent**, **Activity**, **Trash** and **Storage**.

**➕ New** makes a **Note** (plain text or Markdown, as you chose in Settings — the other kind is listed too), a
**Checklist**, a **Sheet** or a **Folder**, or **Upload files**, in the folder you're looking at (or in My docs when
you can only view that folder). **⚡ Quick note** at the top of every page makes a note straight away (see below).
Each item has a **⋯** menu: Open, Favourite, Pin to home, Rename, Move, Tags and colour, Make a copy in My docs,
Share, Transfer ownership, Download (a folder as a `.zip`), Print or save as PDF, Download as PDF, Save PDF here,
History / Earlier copies, Make it Markdown / plain text, Where it's stored, Search in this folder, Leave / Hide,
Delete — each shown only when you may do it.

Files added to your folder outside the app (over Samba, say) show up the next time the folder is opened or
scanned, marked "added outside the app".

## Files, uploads and working with several items

- **⬆ Upload** (or ➕ New → Upload files, or drag files from your computer onto the list or onto a folder) puts
  any file into a folder you can change — My docs, a folder shared with you as Can edit, or a shared folder with
  read and write. A progress panel shows each file. The largest upload is set in App settings (100 MB by default);
  bigger files can still be copied in over Samba. Names Windows can't hold are tidied (`:` becomes `_`, and so on).
- If a file with the same name is already there, the app asks once: **Replace** (the file keeps its shares and
  favourites; the previous copy stays under ⋯ → **Earlier copies** for 7 days — for a note or checklist it goes
  into History), **Keep both** (the new one gets " (2)") or **Skip**.
- Opening a file shows its **file panel**: a preview for pictures (PNG, JPEG, GIF, WebP — the app checks the file
  really is one), its size, who changed it and where it's stored, **Download**, and **Replace with a new copy…**.
  Everything else (PDFs, documents, SVG and HTML files …) downloads and opens in your own apps — the app never
  shows them inside itself.
- **▦ / ☰** switches a folder between a grid (with picture thumbnails) and a list; the choice is kept on this
  device.
- **Tick boxes** select several items; the bar above the list then offers **Move…**, **Copy to My docs**,
  **Share…**, **🏷 Tag…**, **Download (.zip)**, **Favourite** and **Delete** (each when every ticked item allows it).
- On a computer, **drag** items onto a folder (or onto a folder in the path at the top) to move them there.
- **Download (.zip)** on a folder (or a whole shared folder from its ⋯ menu) streams a `.zip` of everything in it
  — up to 10 000 files and 4 GB at a time; hidden files and links are left out.

## Notes

A note is a plain-text file (`.txt`) or a **Markdown** note (`.md`). Type and it saves itself a second after you
stop (and when you leave); the header says **Saved**, **Saving…** or **Offline — will retry**. **Wrap lines** and
**Monospace** change how it looks on this device. The title is the file name: tap it to rename.

- **🔍 Find & replace** (or **Ctrl+F**): Enter / ↓ for the next match, Shift+Enter / ↑ for the one before, **Aa**
  to match case, **Replace** one at a time or **All**. People who can only view get Find.
- **Links**: web addresses (`https://…`) in a note are listed under it, tap to open them (in a new tab).
- **Links to other documents**: type `[[` (or tap **[[ ]] Link**) and pick a document or folder you can see — the
  note gets `[[Its name]]`, readable in any editor, and the link keeps working when the document is renamed or
  moved. A document shows **Linked from** with the notes that link to it (only those you can open). A link to
  something you can't open shows "🔒 No access" (and nothing about it); one to something deleted shows
  "Deleted". A link typed by hand, or one in a file written elsewhere, is found by the name.
- **Markdown notes** show **Edit / Preview / Split** (side by side on a computer): headings (`#`), **bold**
  (`**…**`), *italic* (`*…*`), `code`, code blocks, quotes (`>`), bulleted and numbered lists, tick boxes (`- [ ]`
  — tick them in the preview), links, horizontal rules (`---`) and simple tables. The small toolbar adds bold,
  italic, a heading, a list, a tick box or a link. Nothing in a note is ever run or loaded: HTML shows as text,
  only `http`/`https` links are made, and pictures in Markdown aren't loaded.
- **⋯ → Make it a Markdown note / Make it plain text** renames the file (`.txt` ↔ `.md`); shares, tags, links and
  history stay. A note that is only tick boxes would be a checklist as a `.md`, so add a line of text first (or
  make a checklist).

The file keeps its own line endings and byte-order mark, so a note made on Windows stays a Windows file. A `.md`
file written elsewhere that isn't a pure task list opens as a Markdown note.

### ⚡ Quick note

**⚡ Quick note** at the top of every page — or **N** on a keyboard — makes a note in **My docs → Inbox** called
"Note 2026-10-03 20-41" and opens it. When you leave it, it's named after its first line ("Call the plumber
about the sink"); an empty quick note isn't kept. For a Home Assistant dashboard button or a phone shortcut, open
the app's sidebar address with `/quick-note` on the end — `/a0d7b954_household_docs/quick-note`, where the first part
is what the browser shows when you open **Docs** from the sidebar (a button card with *Tap action → Navigate*); the app's own address
with `#quick-note` at the end does the same.

## Checklists

A checklist is a Markdown task list (`.md`): every line is `- [ ] item` or `- [x] item`, two spaces before the
dash for an indented item — it opens as a checklist in any Markdown editor too. Tick items, tap an item's text to
edit it, use ↑ ↓ to reorder, ⇥ / ⇤ to indent, ✕ to delete, and the box at the bottom to add. **Hide ticked**
and **Untick all** are at the top, with "3 of 12 done". Who ticked an item and when shows under it.

Two people can tick and edit the same checklist at once: each change is applied to the file as it is now. If
someone else changed or deleted the item you touched, the app says so and shows the list as it is.

**🔍 Find** (or **Ctrl+F**) shows only the items holding the text. On a phone, **swipe an item sideways** to tick or
untick it.

If a checklist is edited elsewhere into something that isn't a pure task list, it opens as a note until it's a
task list again.

## Sheets

A sheet is a spreadsheet with formulas, saved as an Excel file (`.xlsx`) — or as a `.csv` if you prefer (Settings →
**New sheets as**). It opens in Excel, LibreOffice, Numbers or Google Sheets too, with the numbers already worked
out. ➕ **New → Sheet** makes one; ➕ **New → Import a sheet** turns a `.csv` or `.xlsx` into a new sheet.

**Typing.** Click a cell and type (on a phone: tap the cell, then the bar at the top). **Enter** goes down,
**Tab** goes right, **Esc** cancels. `12%` becomes a percent, `2026-03-09` or `9/3/2026` a date, `'007` stays
text exactly as typed. The bar at the top always shows what's really in the cell (the formula, not its result);
the box on its left shows where you are — type `C14` (or `Car!B4`) there to jump.

**Formulas** start with `=`: `=B2*C2`, `=SUM(B2:B20)`, `=IF(B2>100,"Over","OK")`, `=VLOOKUP("Car",A2:C20,3,FALSE)`,
`=SUMIFS(C:C,A:A,"Food",B:B,">=2026-01-01")`. Tap **ƒ** for every function with an example; typing `=SU`
suggests SUM, SUMIF, SUMIFS. While typing a formula, click cells to put their address in — or, right after `=`, an
operator, `(` or `,`, press the arrow keys: they pick a cell, outline it on the grid and put its address in (hold Shift
for a range like `B2:B5`), and the next operator you type keeps it. `$` keeps a reference
fixed when you fill or copy (`$B$1`); `B:B` is the whole column; `'Car'!B4` is cell B4 on the tab **Car**.
Functions: SUM, AVERAGE, MIN, MAX, MEDIAN, COUNT, COUNTA, ROUND, ROUNDUP, ROUNDDOWN, ABS, IF, AND, OR, NOT, IFERROR,
SUMIF, COUNTIF, AVERAGEIF, SUMIFS, COUNTIFS, AVERAGEIFS, VLOOKUP, HLOOKUP, XLOOKUP, INDEX, MATCH, LEN, UPPER, LOWER,
CONCAT, CONCATENATE, TEXT, TODAY, DATE, YEAR, MONTH, DAY, DAYS, EOMONTH, NETWORKDAYS, PMT. Problems show as
`#DIV/0!`, `#REF!`, `#NAME?`, `#VALUE!`, `#N/A`, `#NUM!` or `#CYCLE!` (a formula that depends on itself) — select
the cell and the line under the grid says what's wrong.

**Working with the grid.** Drag or Shift+click to select; the line under the grid then shows the **Sum, Average,
Count, Min and Max**. **Ctrl+C / Ctrl+V** copy and paste with Excel and Google Sheets (and within the sheet, where
formulas move along). **Ctrl+D** / **Ctrl+R** fill the first row / column of the selection down / right.
**Ctrl+Z** undoes. Right-click a column letter, a row number or a cell — on a phone, press and hold — to insert or
delete rows and columns (formulas that point at them follow), sort A → Z or Z → A, filter a column (only on your
screen; it isn't saved), set a width (or drag a column's edge), freeze rows or columns, or fill. The toolbar has
the number format (**General, Number, Currency, Percent, Date, Text** — Currency is Home Assistant's currency),
fewer / more decimals, **B**old, alignment, **−1** for red negative numbers, **Σ** for a **totals row** under the
data (SUM, AVERAGE, COUNT, MIN or MAX per column — tap a total to change it), **❄** freeze, **⇅** sort, **⏷**
filter, **📈** chart, **🎨** conditional colours, undo / redo, **⬇** export and **🖨** print.

**Tabs** (up to 10) are at the bottom: **+** adds one; tap the current tab (or right-click it) to rename, move or
delete it. Renaming a tab updates every formula that uses it.

**Charts**: select a range — its first row names the series, its first column holds the labels — and tap **📈**:
bar, line or pie, with a title, under the grid or on a tab of its own. They redraw as the numbers change, and in
Excel they're ordinary Excel charts. **Conditional colours** (🎨) colour cells that are greater or less than a
number, between two, contain a text, are dates before or after a day, are the top or bottom N, or are duplicates —
with a fill and/or a text colour; Excel shows them as conditional formatting.

**Export and print** (⬇): the `.xlsx` itself, a CSV of the current tab (the values you see, or the formulas), and
**Print or save as PDF** — the tab with gridlines, the header row repeated on every page, the totals row and its
charts (choose Landscape for wide sheets).

**Saving.** Changes save a second after you stop. Several people can work in the same sheet: only the cells you
changed are saved, so two people changing different cells never get in each other's way. If someone changed the
same cell while you were editing it, the app shows both and asks **Keep mine** or **Use theirs** (the other version
stays under History). Inserting or deleting rows and columns, sorting and tab changes save the whole sheet; if
someone else changed the sheet just then, it reloads with their changes and asks you to do it again.

**`.csv` sheets** keep values and formulas only — formats, widths, colours, charts, the totals row and more tabs
aren't saved in a `.csv`, so the editor hides those and offers **Convert to .xlsx** (the `.csv` is kept under
History). Comma, semicolon and tab separated files all open; a file you re-save keeps its separator and changes
only the cells you changed.

**Excel files from elsewhere.** An `.xlsx` made in another app opens for editing when the app can keep everything in
it. If it has things the app can't keep — macros, pivot tables, pictures, comments, data validation, merged cells,
links to other workbooks, charts made elsewhere, functions the app doesn't have, cell colours or borders, Excel
tables and similar — it opens **read only**, with the list, so saving here never loses anything. **Save a copy to
edit** makes a new `.xlsx` beside it with what the app keeps. A formula the app can't work out shows Excel's last
result, with a small mark in the corner.

**Search** looks inside sheets too (every tab, what each cell shows); a result says where — **Budget › Car ›
C14** — and opens the sheet with that cell selected. In a sheet, **🔍** (or **Ctrl+F**) finds cells on every tab.

**Number style.** Settings → **Number style for sheets** chooses how numbers look and how the numbers you type are
read: the device's own, `1,234.56`, `1.234,56` (a decimal comma: type `1234,5`) or `12,34,567.89` (lakh and crore).
Formulas are the same in every style (`=ROUND(A1,2)` — commas between values). The file is the same whoever opens
it; only the display changes.

## Tags and colours

**⋯ → Tags and colour…** gives a file or folder any number of tags (`taxes`, `car`, `school` — suggestions come
from the tags already in use) and one of eight colours (a dot and a tint on its icon). Tick several items and use
**🏷 Tag…** to add or take off tags, or set a colour, on all of them. Everyone who can see an item sees its tags
and colour; people who can edit it change them. Tags and colours stay with the item when it's renamed or moved
(also when it's moved outside the app, as long as the app recognises the file). **🏷 Tags** in the sidebar lists
your most used tags; the **Tags** page shows them all, and a tap searches for everything carrying one.
Search with `tag:taxes`, `-tag:car` (without that tag), `tag:"house papers"` or `color:red`, or with the Tag
and Colour filters on the Search page.

## Pins

**⋯ → Pin to home** puts up to 8 documents or folders at the top of your home page. Each card shows something
useful: a checklist "3 of 12 done", a note's first lines, how many items a folder has, and — for a sheet — one
cell you choose when pinning (for example *Budget › B4*, "Left this month: €312.40"). Drag a card by **⠿** to
reorder (on a phone too); **⋯ → Unpin from home** takes it off. Pins are yours alone.

## Print and PDF

- **⋯ → Print or save as PDF** on a note, a Markdown note or a checklist prints just the document — its title,
  whose it is and the date, Markdown formatted, checklists with their boxes — and the print dialog can save it
  as a PDF. Sheets print from their editor (🖨).
- **⋯ → Download as PDF** makes the PDF in the app (A4, page numbers). It uses the fonts every PDF reader has,
  which cover English and Western European languages; other alphabets and emoji show as "?" — the app says so,
  and Print → Save as PDF keeps them.
- **⋯ → Save PDF here** puts that PDF beside the document, when you can add files to its folder.

## Activity and following

**🕑 Activity** is one page with four tabs — **Recent** (what you opened lately), **Activity**, **Trash** and
**Storage**. The **Activity** tab lists what changed — added, edited, renamed, moved, deleted, restored, shared with you — in
everything you can open: your own documents, what's shared with you and your shared folders. Changes made over
Samba or the File editor show as "Someone outside the app" (the app finds them when it scans). Edits are grouped
("Alex edited “Budget” 4 times"), and several files added at once are one line ("Alex added 12 items", with
*Show them*). Filter by **who** (everyone, everyone but you, you, outside the app, a person), **where** (My docs,
Shared with me, shared folders, one shared folder) and **type**. It never lists anything you can't open — nor
names a folder you can't open ("moved to Family", not where from), nor an old name from before you could open the
item, nor anything in someone else's Trash; an admin sets how far back it goes (*Keep activity for*, 90 days).

**Follow** a document, a folder or a shared folder (⋯ → **Follow**, also in a shared folder's ⋯) to get a phone
notification when something in it changes:

- at most **one notification per followed item every 15 minutes** — a burst of changes becomes one message
  ("3 new files in House papers › Taxes", "Alex edited “Budget”");
- **never for your own changes**, never for anything you can't open, never any text from the documents;
- **quiet hours** (Settings → You, 22:00–07:00 by default, Home Assistant's time zone): nothing is sent then;
  what happened is told in one message afterwards. The same two times means no quiet hours;
- *Notify me about what I follow* (Settings → You) turns them off. Notifications go to the phones Home Assistant
  links to you (Settings → People → you → Track device) and any extra notify services an admin added.

**🕑 Activity → You follow** lists what you follow, with **Unfollow**.

## Home Assistant sensors

⋯ → **Show in Home Assistant…** on a checklist, a sheet or a folder (owners and people who can edit it; an admin can
turn this off: *Items can be shown in Home Assistant*):

| Item | Sensor | State and attributes |
|---|---|---|
| Checklist | `sensor.docs_<name>_open` | open items; `total`, `done`, and the first 10 open items (unless you tick *Hide item text*) |
| Sheet | `sensor.docs_<name>` | a cell's value (`Budget!B12`), or the sum of the numbers in a range (`Budget!B2:B9`); a unit you choose and `state_class: measurement` for numbers — for a dashboard card like "budget left this month" |
| Folder | `sensor.docs_<name>_files` | how many files are in it (all levels), `newest_file` and `newest_modified` |

The sensor is updated within seconds of a change in the app, and after the app's scan finds a change made outside
it; everything is posted again every 5 minutes (Home Assistant forgets these sensors when it restarts). Turning it
off, deleting the item or the admin's switch removes the sensor — and so does the person who turned it on losing
access to the item (checked before every update; they're told). Everyone who can see the item is told it's on the
dashboard ("📊 On the Home Assistant dashboard as sensor.docs_packing_open").

**Keeping private values out of Home Assistant's history.** Sensor states are kept by Home Assistant's recorder
(history, logbook) like any other. To keep them out, add this to Home Assistant's `configuration.yaml` and
restart Home Assistant:

```yaml
recorder:
  exclude:
    entity_globs:
      - sensor.docs_*
```

(or list single sensors under `entities:`).

## Storage

**💾 Storage** shows your own folder — and each shared folder you can change (the menu at the top):

- size, files and folders, against your limit when an admin set one (*Limit per person*);
- **by type** (a bar per kind of file) and **growth over 12 months** (a bar per month; months before the app started
  keeping a daily note are estimated from when it first saw each file still there — lighter bars);
- **duplicates** — files with exactly the same content (every copy you can open, wherever it is; copies you can't
  open aren't counted, not even in the numbers). **Keep this one**
  moves the other copies you may delete to Trash;
- the **largest files**, files **not changed in** 1–10 years, and **empty folders**;
- what **History** (earlier copies, `.versions`) and **Trash** (`.trash`) take.

**Admin → Storage** shows every person's folder and every shared folder — sizes, counts, limit, History, Trash and
duplicates within each folder (how many and how much space) — the totals by type and growth, and the app's own `/data` (the database).
Admins see sizes and counts only, never file names (the lists are on each person's own page). **Empty all Trash
now** deletes everything in everyone's Trash and the shared folders' Trash for good, after a confirmation.

## Scanning with your phone

➕ New → **📷 Scan** (in any folder you can change; it opens the phone's camera straight away):

1. Take a photo of the page (**📷 Add a page** for the next one; on a computer, choose pictures).
2. Drag the **four corners** onto the page's corners — the page is cut out and straightened. **↻ Rotate** turns the
   photo, **Whole photo** keeps all of it.
3. **Black and white** makes text crisp (even in uneven light).
4. **Save as** one **PDF** (made in your browser, a page per photo) or **JPEG pictures**; the name comes from your
   pattern (Settings → You → *Scan file names*, `Scan {date} {time}` by default; `{folder}` and `{n}` work too).

The scan is saved in the folder you're in. With AI set up, **✨ Read text** is offered straight after (below).

## PDFs in search

The text inside PDFs (up to 500 pages, PDFs up to *Largest PDF whose text is searchable*, 20 MB) is read in the
background — in a separate process that is stopped if a PDF takes too long or needs too much memory — so search
finds it; a result says which page ("page 3: …"). A PDF's file panel says whether its text is searchable:
*Password-protected* PDFs can't be read; *Scanned — no text* means it is pictures of pages (AI can read those).
PDFs still download to open; the app never shows them itself.

## Importing

- **➕ New → Import from Google Keep**: in Google Takeout (takeout.google.com) choose only **Keep**, export, and
  choose the `.zip` here. The app shows what it found first (notes, lists, archived, pinned, pictures, labels;
  what's already imported or in Keep's bin is skipped), then imports into **My docs → Google Keep**: notes become
  `.txt` notes, lists become checklists (ticks kept), the title (or first line) is the name, the times are kept,
  labels become tags, colours come along, pinned notes become ⭐ Favourites, archived ones go to **Keep archive**,
  and attached pictures are saved beside the note. Importing the same Takeout again adds only what's new.
- **➕ New → Import a .zip**: unpacks a `.zip` into a new folder (named after it) in the folder you're in. Hidden
  files, links, password-protected entries, anything that would land outside the folder (`../`, absolute paths)
  and entries that unpack suspiciously much (a "zip bomb") are skipped and listed; names Windows can't hold are
  tidied. At most 10 000 entries and 4 GB unpacked; the upload limit applies to the `.zip`.

## AI (optional)

An admin can connect an AI model in **Admin → App settings → AI**: your own **Ollama**, an **OpenAI-compatible**
service, or **Anthropic Claude** — address, access key, a *text model* and a *vision model* (for pictures), and a
monthly token limit. **Until then nothing is sent anywhere and no AI buttons are shown.** Every action asks first,
in a dialog that names the provider and the model and says what will be sent; nothing is sent before you press
Send. *Show AI buttons* (Settings → You) hides them for you. The provider may keep what it receives under its own
terms — your own Ollama keeps everything in your house.

**What each action sends:**

| Action | Where | What is sent to the AI provider |
|---|---|---|
| **✨ Read text with AI** | a picture's or PDF's file panel, ⋯ (people who can edit it) | the picture (PNG, JPEG, GIF or WebP), or the scanned pages of a PDF (its JPEG page pictures, at most 10) to the vision model, with "write out the text". The text is kept in the app's database — never written into the file — searchable, shown as *Text read by AI* in the file panel, and editable by editors. |
| **✨ Read text from scans here** | a folder's ⋯ (switching it on: the folder's owner or a manager — in a shared folder, people who can change it; switching it off: anyone who can edit it) | once switched on, each picture and scanned PDF in that folder and its folders, as above, a few a minute, until switched off |
| **✨ Summarise with AI** | ⋯ on a note, checklist or a PDF with text | the document's name and its text (at most 100 000 characters). *Save as note* keeps the summary as a Markdown note beside it. |
| **✨ Checklist from text (AI)** / ⋯ → *Make a checklist from this* | ➕ New, a note's ⋯ | the text you pasted, or the note's text; you check the items before the checklist is made |
| **✨ Sheet from a table (AI)** | ➕ New | the table you pasted, or one photo of a table (made smaller first, to the vision model); you check the rows before the sheet is made |
| **✨ Ask about this folder (AI)** | a folder's ⋯ | your question, and the names and text of up to 30 files in that folder (and its folders) that you can open — at most 200 000 characters, newest first. The answer links the files it used. |
| **Test connection** | Admin → App settings | asks the provider for its list of models, and the text model one tiny question |

| **✨ Suggest a name and folder** | a file's panel (people who can edit it) | the file's text (its PDF text or text read by AI, at most 20 000 characters), its name, and the names of the folders in the same space you can change (at most 150). The answer is only shown — **Accept** renames and moves it, **No thanks** leaves it. |

Nothing else — no other documents, names of people, or anything about your Home Assistant — is sent. **Admin → AI
usage** lists every request (what for, the model, tokens, time, whether it worked — never what was sent), with
totals by day, action and model, this month's use against the limit, and an estimated cost when you enter your
provider's prices. Past the monthly limit, AI actions say so until next month.

## Send to a chat, and checklists to Todo

When the household also uses **Household Chat** or **Household Todo** (both running in Home Assistant), Docs can
hand things to them. The menu entries appear only while the other app is there and has said so (**Admin → App
settings → Connected apps** lists them).

**⋯ → Send to chat…** on a document, folder or file: Docs asks Chat for the chats *you* can post in (a spinner while
it answers; if Chat isn't running or doesn't answer within two minutes, the dialog says so with *Try again*). Each
chat shows its name and how many people are in it. Pick one and **Send**: Chat posts a card "**You shared Trip
plan**" with the kind of item, whose it is and **Open in Docs** — never what's in it.

- **Also give the chat's members access** (owners and managers of the item only): Docs then asks Chat who is in
  *that* chat and lists the people who use Docs but can't open the item yet, each ticked — untick anyone who
  shouldn't get it. Once Chat has posted the card, the people you left ticked (and still in the chat) get **Can
  view** (or **Can edit**) — a normal share you can change or remove in Share…, and they're told as usual. Nobody
  else, whatever an answer from another app says.
- **Open in Docs** opens the item in Docs inside Home Assistant. Someone who can't open it sees Docs' own
  **🔒 No access** page — it doesn't show the item's name or whose it is (the card in the chat says who shared it),
  and it looks the same for something deleted.
- Chat decides whether you may post there (you must be in the chat and allowed to write); Docs decides who may open
  the item.

**⋯ → Make a Todo list…** on a checklist:

- **Where**: a new list (named after the checklist; *Shared with the household* or *Just me*) or a list you already
  have in Todo (Docs asks Todo for them).
- **Items**: tick the ones to send — open items are ticked; ticked (done) items are left out unless you tick
  **Include ticked items**. An item indented under another becomes part of that task's checklist in Todo.
- **Keep them here too** (the default) or **Move them** (people who can edit the checklist): moved items leave the
  checklist only once Todo has them.
- Todo makes the tasks, marked "from Docs"; dates, who does what and reminders are set in Todo. A long checklist goes
  in several parts, one after the other. If Todo says no — for example "Open Household Todo once first" — nothing
  more is sent and nothing leaves the checklist.
- The checklist shows a note: "✅ Sent to Todo › Weekend, 5 items" (a personal list's name only to you).

What travels between the apps goes over Home Assistant's event bus: names, ids and titles — and, for Todo, the text
of the items you chose. Never anything else from your documents. Exactly:

| When | Docs sends | The other app answers |
|---|---|---|
| Send to chat… opens | to Chat: your Home Assistant user id, how many chats (50) | your chats: id, name, kind, icon, member count (no one's user id) |
| *Also give the chat's members access* ticked | to Chat: your user id and the chosen chat's id | that one chat, with its members' user ids |
| Send | to Chat: your user id, the chat's id, the item's **title** (a document's name without `.txt` / `.md` …), its **kind** (note, checklist, sheet, folder, file), its Docs id, the **owner's name** (not for shared folders), the app's sidebar path and the item's link (`/doc/<id>`), whether members get access, the label "Docs" | the message's id; with *members' access*, the members' user ids |
| Make a Todo list… → *A list I already have* | to Todo: your user id | your lists: id, name, shared or personal, open tasks |
| Send to Todo | to Todo: your user id, the list's id — or a new list's name and *shared* — and for each item you ticked its **text** (one line, at most 200 characters), done or not, and whether it's under another item; the label "Docs" | the list's id and name, how many tasks and sub-items were made |

Every message also carries the envelope the apps share: a message id, the two apps' names, its kind, when it was
sent and when it expires. To keep these messages out of Home Assistant's history, add to `configuration.yaml`:

```yaml
recorder:
  exclude:
    event_types:
      - household_apps
```

## Templates

**➕ New → From a template…**: built-in **Meeting notes**, **Home maintenance log** (a sheet: date, what, who, cost),
**Monthly budget** (a sheet with categories, planned and spent, what's left and totals), **Emergency contacts**,
**Babysitter info** and **Trip plan** — plus the household's templates and your own. The new document goes into the
folder you're in (or My docs).

**⋯ → Save as template…** on a note, checklist or sheet you can open keeps a copy of it as it is now among **your**
templates; admins can tick *For the whole household*. Templates are plain files in hidden `.templates` folders (in
your own folder, and in the documents folder for the household's) — remove one with ✕ in the template list.

## Filing and clean-up rules

**⋯ → Filing and clean-up rules…** on a folder you can change (or on a shared folder's top, ⋯, with read and
write):

- **Filing rules** act on files that **arrive** in that folder — uploaded, scanned, or added over Samba / the File
  editor and found by the app — not on documents made in the app. A rule matches the **name** (wildcards as in
  search: `Power-bill*.pdf`, `IMG_????.jpg`, `[0-9]*`; case doesn't matter), and optionally the **type** (notes,
  checklists, sheets, PDFs, pictures, other files) and a **size**. It **renames** the file — `{date}` (2026-10-04),
  `{yyyy}`, `{mm}`, `{dd}`, `{name}` (its name now), `{n}` (1, 2, 3 … the first free number); the extension stays —
  and/or **moves** it to a folder in the same space. Example: `Power-bill*.pdf` → rename to `{yyyy}-{mm}
  Electric.pdf`, move to *Bills › Electric*.
- Rules are tried in order (↑ ↓); **the first that matches** acts. **Show what would happen** lists what a rule would
  do with the files already in the folder, before you save it (those stay as they are).
- A rule acts as the person who made it, with their rights: if they can no longer change the files, it does nothing.
  Each action is one line in **🕑 Activity** ("Your filing rule filed …") with **Undo** for 7 days, which puts the
  file back where it arrived under its old name.
- Rules can't be set on the very top of My docs (make a folder — Inbox — for arrivals).
- **Clean-up**: *Move to Trash* or *Move into an "Archive" folder here* after **N days without a change**. Once a
  day; only the files and documents directly in that folder; folders, anyone's favourites and pinned items are left
  alone; what goes to Trash can be restored until Trash is emptied. The folder says so: "Items here move to Trash 30
  days after their last change".
- With AI set up, a file's panel has **✨ Suggest a name and folder** (see AI) — only a suggestion.
- Nothing is filed or cleaned up while documents are being moved (read-only mode); arrivals wait until it's over.

## Kids' space

An admin can mark a person as a **child** (Admin → People → **Child**). A child's Docs is simpler:

- their own **My docs**, what people share **with them by name**, and admin shared folders marked **Kids folder**
  — never what's shared with Everyone, and no other shared folders;
- notes and checklists (Markdown too), uploads, scans and Quick note — **no sheets**;
- **no sharing** (Share…, Transfer, Send to chat), **no AI**, and no regular-expression search.

These are enforced by the app itself, not just hidden. **Let parents view…** (Admin → People, on a child) chooses the
people who may open — not change — what the child makes in their My docs **from the moment they were marked as a
child** (through the app); what they had before, and files added outside the app, stay theirs alone. Parents find
it under **🧒 Kids' docs**. **Admins decide this**: any admin can mark any person as a child and choose any other
person — another admin included — as a parent; only naming themselves is refused (another admin has to). So an
admin and a person they name can, together, read what someone makes after being marked as a child: that's how the
feature works, not a loophole, and it can't happen silently. The person is told
when they're marked as a child (or not any more) and who can view their docs — a notification on their phone, and a
line at the top of their My docs. Marking someone as a child and choosing parents are kept in the app's audit log. A
**Kids folder** is an admin shared folder with *Kids folder* ticked (Admin → Shared folders): give the children (and
whoever else) access as usual.

## What changed since you last looked

- A **dot** (●) marks documents and files someone else changed (or something outside the app) since you last opened
  them — in folders, spaces and search. Things that arrived since you started using this count too.
- **Opening** one you'd seen before says "Changed by Alex since you last looked" — for a note, **Show what changed**
  lists the lines removed and added since the version you saw; in a checklist the new or changed items, and in a
  sheet the changed cells, are highlighted for that visit. (When History no longer has the version you saw, it just
  says who changed it.)
- **✓ Mark all as seen** in a folder clears the dots there (and in the folders inside it).
- In search, `is:new` (or **● Changed since you looked** under *Is*) finds them all.
- Your own changes never count.

## Sharing

**Share…** (on anything you own or manage) adds people or **Everyone**, each with:

- **Can view** — open, search, download and *Make a copy in My docs*. On a checklist (or a folder of them),
  **Viewers may tick items** lets them tick (on by default; untick it in the share).
- **Can edit** — also edit, make things inside a shared folder, rename, move within the shared folder, and
  delete (to the owner's Trash). Things made inside someone's folder belong to its owner.
- **Manager** (the owner picks this) — also share with others and remove people, move things anywhere in the
  owner's folder, and delete shared items.

A folder's share reaches everything inside it, now and later — including files added outside the app. A share
deeper down can add access, never take it away. Everyone shares start as **Can view** (an admin can change
that, or turn Everyone sharing off).

- **Leave** removes something shared with you directly; **Hide** keeps it out of Shared with me / Everyone (an
  Everyone share can only be hidden).
- **Transfer ownership** (owner only) gives an item to someone else: it moves into their folder with its shares,
  favourites and history, and you keep Can edit.
- People get a phone notification — "Alex shared 'Trip 2026' with you" — through Home Assistant (their phones
  from **Settings → People → Track device**, plus any extra notify services an admin adds). Each person can turn
  it off in **Settings**. Sharing with Everyone doesn't notify anyone, and notifications never contain text from
  the document.

## Saving, conflicts and history

- Every save writes a new file next to the old one and swaps it in, so nobody ever sees half a file.
- If someone else saved a note after you opened it — or it was changed outside the app — saving asks
  **Keep mine** (yours is saved; theirs goes into History), **Use theirs** (your changes are dropped) or
  **Compare** (the lines that differ). An open document checks for changes every 10 seconds and shows
  "Alex is editing" when someone saved in the last half minute.
- **History** (⋯) lists earlier versions — who wrote each one, or "changed outside the app" — with **View** and
  **Restore** (what's there now becomes a version too). A version is kept when someone else edits the document,
  at most every 5 minutes while one person edits, on every conflict and restore, and the first time the app saves
  over a change made outside it. Versions follow a document through renames and moves.

## Shared folders

An admin can open an existing folder in `/share` — say `/share/Documents/House` — to chosen people, each **Read
only** or **Read and write**, or to **Everyone** (people added later get it too). It shows under **📁 Shared
folders** with its name ("House papers").

- **Read only**: open, search, preview, download (also the whole folder as a `.zip`) and copy into My docs.
- **Read and write** adds: new notes, checklists and folders, uploads, rename, move within the folder, and delete —
  into the folder's own hidden `.trash` (Trash shows a section for each shared folder you can change, and anyone
  who can change it can restore). Earlier versions of documents there are kept in its hidden `.versions`.
- Everyone with access sees the same files; there's no per-item sharing inside a shared folder.
- The files are ordinary files in that folder — what's changed over Samba shows up in the app, and the other way
  round.
- If the folder's storage isn't connected, it disappears from everyone's list until it's back (admins see ⚠).

## Trash

**Delete** moves a file or folder to its owner's **Trash** (in `.trash` inside the documents folder, with a note
of where it was; things in a shared folder go to that folder's own Trash). What's in your Trash is yours alone: the
people it was shared with no longer find it — not in search (not even with *Trash* ticked), Activity, follows,
links or pins; a shared folder's Trash is seen only by people who can change that folder. **Restore** puts it back
into the folder it came from — that same folder, even if it was renamed or moved since (never another folder that
now has the old name) — or into the top of My docs (or the shared folder) if its folder is gone, with "(restored)"
added if the name is taken — with its shares and history. **Empty Trash** deletes for good; items are emptied
automatically after the number of days in App settings (30 by default).

## Search

The box at the top (**/** or **Ctrl+K** on a keyboard) opens the **Search** page. It looks through everything you
can open — My docs, Shared with me, Everyone and your shared folders — by name and by the text inside notes,
checklists and other text files (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.log`, `.xml`), and never shows
anything you can't open.

**In the box**

| Type | Finds |
|---|---|
| `budget car` | both words, in the name or the text (each word matches the start of a word) |
| `"old town"` · `-draft` · `tent OR stove` | the exact phrase · without that word · either word |
| `*.pdf` · `bill-2026-??.pdf` · `IMG_[0-9]*` | names by pattern: `*` any text, `?` one character, `[0-9]` one of these |
| `name:/^invoice-\d{4}/` · `content:/\b\d{4}-\d{4}\b/` | a regular expression in names · in the text |
| `type:pdf` · `ext:xlsx` | a type (note, checklist, sheet, folder, image, pdf, spreadsheet, document, archive, video, audio, other) · an extension |
| `modified:7d` · `modified:>2026-09-01` · `modified:2026-01-01..2026-03-31` · `created:<2025` | changed (or first seen) in the last 7 days · after a day · between two days · before 2025; also `today`, `yesterday`, `thismonth`, `lastmonth`, `thisyear`, `lastyear` |
| `size:>5mb` · `size:100kb..1mb` | bigger than 5 MB · between (b, kb, mb, gb) |
| `by:alex` · `by:me` · `by:outside` · `owner:priya` | last changed by Alex · by you · outside the app · in Priya's folder |
| `in:mine` · `in:shared` · `in:"House papers"` · `in:trash` · `path:taxes/2026` | where to look · inside those folders |
| `is:fav` · `is:open` · `is:done` · `is:empty` · `is:dup` · `is:shared` | favourites · checklists with open items / all done · empty folders · duplicates · shared |
| `shared:byme` · `shared:withme` · `shared:not` · `access:edit` · `access:read` | sharing · what you can change |
| `tag:taxes` · `-tag:car` · `tag:"house papers"` · `color:red` | carrying a tag · not carrying it · a tag with a space · a colour (red, orange, yellow, green, teal, blue, purple, grey) |

**The panel** below the box sets the same things with buttons: where to look (Everywhere, This folder — from a
folder's ⋯ → *Search in this folder* — My docs, Shared with me, Everyone, all or one shared folder, Include
Trash), what to match (name or text, name only, text only), how names match (contains, starts with, exact, ends
with, wildcard, regular expression) and the filters (modified, created, size, modified by, owner, shared, access,
extension, path, tag, colour, type and the others). Every filter in use shows as a chip above the results — tap it to remove
it.

**Results** show the name (the match highlighted), where it is, when it changed and by whom, size and type. Sort
by relevance, name, modified, created, size, type or location; group by location, type or date; up to 500
results, 50 a page. Opening a note found by its text opens it at that line; ⋯ → **Show in folder** opens the
folder it's in. Tick results to download them as a `.zip`, copy, move, favourite or delete them together.
**⬇ CSV** saves the list (names, places, dates, sizes — never any text from the files).

**☆ Save search** keeps a search (pinned ones are in the sidebar under Saved searches); the page also lists your
last 20 searches. Words search as you type; patterns run when you press Enter. A regular expression runs on its own
and is stopped after 5 seconds ("Search stopped after 5 s — narrow it down"); one at a time per person, and an
admin can turn them off in App settings.

## Settings

**Settings** (everyone): tell me when something is shared with me, notify me about what I follow and quiet hours,
how folders are sorted, whether new sheets are `.xlsx` or `.csv`, the number style for sheets, whether new notes are
plain text or Markdown, scan file names, show AI buttons (when AI is set up), the theme, How the app sees you, and
where your folder is.

### App settings (Admin → App settings)

They apply at once.

- **People and sharing** — *Name of each person's folder* (their Home Assistant display name or user name; used
  only when a folder is first made), *New people get access* (on), *People may share with Everyone* (on), *Role
  picked first when sharing with Everyone* (Can view).
- **Documents** — *Versions kept per document* (30, 5–200), *Days before Trash is emptied* (30, 1–365),
  *Largest document the editor opens* (5 MB, 1–25; bigger files download), *Largest upload* (100 MB, 1–2048),
  *Limit per person for their own folder* (0 = none), *Warn when a document looks like it holds a password* (on).
- **Finding files** — *Look for changes made outside the app every* (10 minutes, 2–120), *Largest text file whose
  words are searchable* (1 MB, 0–20; 0 = names only), *Allow regular-expression search* (on).
- **Finding files** also has *Largest PDF whose text is searchable* (20 MB, 0–64; 0 = PDFs by name only).
- **Activity, Home Assistant and imports** — *Keep activity for* (90 days, 7–730), *Items can be shown in Home
  Assistant* (on), *Import from Google Keep* (on).
- **AI** — *AI features* (off), *Provider*, *Address*, *Access key* (never shown again once saved), *Text model*,
  *Vision model* (empty: the text model), *Most tokens a month* (0 = no limit), prices per million tokens (for the
  cost estimate only), and **Test connection**.
- **Backups** — *Backup includes the documents themselves* (off).

### The app option (Configuration tab)

`admin_users` — the Home Assistant user names or user ids (not display names) of the admins. Restart the app
after changing it.

## Admin

- **Documents folder** — where the documents are, whether it's reachable, free space, how many files, **Rescan
  now**, (until documents are in use) choosing a different folder with a folder browser over `/share`, and
  **Move to a new location** (below). A new or
  empty folder is set up by the app (a hidden `.household_docs` marker, `people/`, and the hidden `.trash`,
  `.versions` and `.tmp`). Refused, with the reason: `/share` itself, a folder of another install, one inside (or
  containing) Household Chat's files folder or an admin shared folder, a read-only folder, a missing one (storage
  not connected) and a folder that already holds other files. The app never copies, moves or deletes the folder
  as a whole.
- **Shared folders** — **➕ Add shared folder**: browse `/share` (or type a path), give it a name, and choose
  **No access / Read only / Read and write** for Everyone and for each person (tick **Kids folder** for one children
  may see). The page says straight away whether
  the folder can be shared; it refuses `/share` itself, hidden folders, the documents folder (or anything inside or
  around it — that would show everyone's documents), Household Chat's files folder, another install's documents
  folder, and a folder inside or around another shared folder. This is checked again every time a shared folder is
  used: if Household Chat's files folder (or another install's documents folder) later turns up inside a shared
  folder, the app leaves that part out — nobody sees it in lists, search or downloads — and the shared folder shows
  it as a problem here. Each shared folder shows who has what, how many
  files and how big, and the last scan; **Edit** (name and access), **Rescan now** and **Stop sharing** (people lose
  access in the app; nothing on disk changes).
- **People** — everyone with a Home Assistant login: **Access** on or off (turned-off people see "An admin has
  turned off Household Docs for you"; their folder stays, and stays shared), **Child** (Kids' space, with **Let
  parents view…**), their folder name (**Rename folder**), how many files and how much space, their phones and
  extra notify services with **Send a test**, and **Delete their documents…** (moves everything in their folder to
  their Trash; you type the folder name to confirm). Admins see counts and sizes, never anyone's documents. The
  folder browser doesn't look inside the documents folder or Household Chat's files folder (no one's folder names).
- **App settings** — above, and **Connected apps**: the other household apps Docs talks to (Household Chat,
  Household Todo), their version, when they were last heard from and what they can do. Read only.
- **Storage** — every folder's size and counts, History, Trash, `/data`, and *Empty all Trash now* (see Storage).
- **AI usage** — every request to the AI model (see AI).
- **Backup** — below.

### Moving the documents to a new location

To put the documents somewhere else — on network storage, say, or a Samba share of their own — you copy the
folder, the app checks the copy, then switches. The app itself never copies, moves or deletes the folder.

1. **Choose the new location** (type it or **Browse…**): any folder inside `/share` that isn't the documents folder,
   inside it or around it, another install's, Household Chat's, or overlapping a shared folder. It may be new,
   empty, or already hold your copy.
2. **Pause changes** — read-only mode. Everyone sees "Documents are being moved — you can read but not change
   anything until *you* finish"; opening, searching and downloading still work, and Trash clean-up and version
   pruning wait. Recommended, so nothing changes after you've copied.
3. **Copy the whole folder, hidden folders included** (`.household_docs`, `.trash`, `.versions`). The page shows
   the exact commands and paths: with the **Terminal & SSH** app
   (`rsync -a "/share/household_docs/" "/share/nas/household_docs/"`, or `cp -a`), with the **Samba** app from a
   computer (turn on *Show hidden files* first), or with the File editor or your NAS's own tool.
4. **Check copy** compares the two folders: files, folders and size on both sides; what's **missing** in the new
   location, what's **different** (size or modified time — **Deep check** compares the contents with SHA-256,
   slower, with progress, so a copy that didn't keep times is fine) and what's **only in the new location**;
   whether `.trash`, `.versions` and the marker came along (without them, Trash and History don't); free space.
   Lists show up to 200 lines; **Download full report** has them all (CSV).
5. **Switch** — when nothing is missing or different (otherwise **Switch anyway…** asks you to type how many). The
   app writes its marker in the new folder, notes the move in the old folder's marker (the only change made there),
   uses the new folder from then on — every share, tag, link, tick, favourite and earlier version stays — scans
   it, turns read-only mode off and tells everyone ("Documents moved to the new location").

The old folder is left exactly as it was: Admin shows "Previous location: … — no longer used; delete it yourself
when you're happy", with **Switch back…** while it's still there (the same check and switch, so anything changed
since shows as different). Shared folders aren't affected. Each step is in the app's audit log.

## Who can see what, and where your data goes

- **In the app**, a document is visible only to its owner, the people it (or a folder above it) is shared with,
  and Everyone when it's shared with Everyone; a file in a shared folder only to the people the admin gave that
  folder to. Everyone else gets "not found" — search included. Admins get no extra access to documents through
  the app's pages (an admin who wants a shared folder gives it to themselves too; the folder browser doesn't list
  people's folders). One thing admins *can* do by design: mark a person as a child and choose who may view what
  that person makes from then on — anyone but themselves (see Kids' space). The person is notified and it's in the
  audit log.
- **But anyone who administers Home Assistant can read the files on disk and in backups.** That's not something an
  app can prevent: they can open `/share` (File editor, Samba, the Terminal app), download a backup with *Backup
  includes the documents themselves*, restore a database of their own making, or read the copy check's report
  (which lists every file's path). Admin downloads of backups and copy-check reports are kept in the audit log.
- **Outside the app** the documents are ordinary files in `/share`: anyone who can reach `/share` can read and
  change everyone's documents, and the app's sharing doesn't apply there.
  - To keep the documents folder off your Samba share, choose a folder the Samba app doesn't share — or put it on
    network storage with its own share and password (mounted under `/share`) — on Admin → Documents folder before
    anyone has documents, or later with **Move to a new location**.
  - Home Assistant backups that include **Share** contain everyone's documents: keep them as safe as the
    documents themselves.
- **Home Assistant** gets notifications ("X shared 'Y' with you", follow notifications: names and document titles,
  never their text) and the sensors people turn on (a checklist's open items, a sheet's cell, a folder's file
  count — see Home Assistant sensors). The app reads the people and their phones from Home Assistant.
- **Household Chat and Household Todo** (when you send something there): a chat card's title, kind, owner's name
  and ids; for Todo, the text of the checklist items you chose. Through Home Assistant's event bus, which Home
  Assistant's history records unless you exclude `household_apps` (see Send to a chat). Home Assistant admins,
  automations and every installed app with Home Assistant API access can see these events (and could send one), so
  Docs never acts on an answer beyond what you confirmed: member shares go only to the people you ticked.
- **An AI provider** gets exactly what the table under AI says — only after an admin set AI up, and only when
  someone presses Send (or switched on reading scans in a folder).
- Nothing else leaves your Home Assistant. The app has no port on your network: it is reachable only through Home
  Assistant's sidebar (ingress), and it refuses requests that don't come through it.

## Backups

- **Admin → Backup → Download backup** — its size is shown next to the button; a zip with the database (sharing, the index, ticks, favourites,
  settings). With *Backup includes the documents themselves* on, it also holds every file in the documents folder
  (people's folders, Trash and History), which can be large.
- **Restore** — replaces the database with the backup's (no merge, no undo), keeps this install's documents
  folder, then re-scans every folder: the index is rebuilt from what's on disk, and sharing for files that aren't
  there is kept for 30 days in case they come back (say, when you restore Share afterwards). Files in the zip are
  put back only into an empty documents folder, unless you tick **Replace files**.
- Home Assistant's own backups of the app include the database; the documents are in Home Assistant's backups
  when **Share** is ticked.
- Backups leave out access keys and passwords; after restoring on a new install, enter them again. (Restoring keeps the AI access key this install already has.)
- A backup with the documents holds **everyone's** documents, and whoever restores one decides what the database
  says (who may open what). Downloading and restoring are for admins only and are kept in the audit log — keep
  backups as safe as the documents themselves.

## Limits

Folders 10 deep, 50 000 files and folders per person's folder (and per shared folder) for the index, checklists
2 000 items, sheets 10 tabs of 5 000 rows × 100 columns and 200 000 filled cells (an `.xlsx` from elsewhere that's
bigger, unpacks to more than 64 MB or takes more than 20 seconds to read isn't opened — download it instead), names up to 200 characters (no `/ \ : * ? " < > |`, no leading dot — names Windows can hold), the
editor's and upload limits from App settings, `.zip` downloads up to 10 000 files and 4 GB, search results up to
500, regular expressions up to 200 characters and 5 seconds; PDF text up to 500 pages and 30 seconds a PDF; imports
up to 10 000 entries, 4 GB unpacked and 5 000 Keep notes; 200 follows per person and 200 Home Assistant sensors;
AI: 30 files and 200 000 characters for a question, 10 pages a scan. Dates follow Home Assistant's time zone.

## Troubleshooting

- **"No admin yet" on every page** — add the user name it shows to `admin_users` on the app's Configuration tab,
  save and restart the app.
- **I'm in `admin_users` but not an admin** — check **How the app sees you**: use the user name or user id shown
  there (not the display name), and restart the app after saving.
- **"Documents folder not found — is the storage connected?"** — the documents folder (or the network storage it
  is on) isn't there. Nothing is written until it's back; the app picks it up again within 5 minutes (or press
  Rescan now on Admin → Documents folder).
- **A file I added over Samba doesn't show** — open its folder (that looks at the folder at once) or wait for the
  next scan. Hidden files (starting with a dot) and links are never shown.
- **A file moved or renamed over Samba lost its shares and history** — the app follows a file renamed or moved
  outside it only within the same person's folder (or the same shared folder), and only when it's clearly the same
  file (same contents, or the same time and size). Moved into someone else's folder or a shared folder, it's a new
  file there, without the old one's shares, tags or history — on purpose, so nothing private goes along. Use
  **Transfer ownership** or **Make a copy** in the app instead.
- **Someone can't see what I shared** — check they have access (Admin → People) and look at **Share…** on the
  item: shares from folders above are listed there too.
- **A shared folder disappeared** — its storage isn't connected, or the folder was renamed or replaced by a link
  outside the app; Admin → Shared folders says which.
- **"Search stopped after 5 s"** — the regular expression took too long: add words or filters (they narrow what it
  looks through) or simplify the pattern.
- **A sheet opens read only** — it was made in another app and has something the app can't keep (the list is at
  the top). Use **Save a copy to edit**, or edit the original in Excel or LibreOffice.
- **"This .xlsx couldn't be read safely"** — it's very large, damaged, password-protected, or contains something the
  app refuses for safety (it is never changed). Download it instead.
- **"Documents are being moved — you can read but not change anything"** — an admin turned on read-only mode to
  move the documents. It ends when they switch to the new location or turn it off (Admin → Documents folder).
- **A PDF shows "?" instead of some letters** — Download as PDF uses the standard PDF fonts (English and Western
  European letters). Use **Print or save as PDF** for other alphabets and emoji.
- **No "Send to chat…" or "Make a Todo list…" in the menu** — the other app isn't installed, isn't running, or hasn't
  said hello in the last day: restart it and look at Admin → App settings → Connected apps. Both apps must run
  inside Home Assistant.
- **"Open Household Todo once first"** — Todo only takes tasks from people who have opened it; open Household Todo
  from the sidebar once, then send again.
- **Open in Docs shows 🔒 No access** — the item isn't shared with you (or was deleted): ask the person who shared
  it in the chat to share it with you in Docs.
- **A filing rule didn't act** — rules act on files that *arrive* after the rule is saved, as the person who made it
  (they must still be able to change the file and the destination); not while documents are being moved.
- **Something else** — the app's **Log** tab in Home Assistant (Settings → Apps → Household Docs → Log).
