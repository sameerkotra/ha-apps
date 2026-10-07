# Household Docs — design spec

Status: **built (2026-10-04)** — all steps at once; §16 answered with the drafted defaults (see §16). Steps 1–12 (app setup, notes, checklists, sharing, admin shared folders, search, sheets with tabs, charts and conditional colours, polish — moving the documents folder, Find & Replace, number styles — organise — tags, Markdown, links, PDF, pins, Quick note — stay up to date — activity, follows, HA sensors, storage report — bring things in — PDF text search, camera scan, Google Keep and .zip import — optional AI, and connect and tidy — Send to chat, checklist → Todo, templates, filing and clean-up rules, Kids' space, what changed since you last looked) are built; the notes marked *(built)* below record where the code settled a detail the spec left open. The developer reference for a new app in the household family. The user guide will
be `DOCS.md`.

Household Docs is a Home Assistant app for everyday documents: **notes, checklists and sheets** (a spreadsheet with
formulas), kept in **folders**, **shared** with other people, plus **existing `/share` folders** that an admin hands out
read-only or read-write. It has strong **search** across everything a person can open — by file name, pattern, date
modified, type, size, who changed it, and the text inside (§10).

**Every document and file is a real file under Home Assistant's `/share`** (§5): notes are `.txt` / `.md`, checklists
`.md`, sheets `.xlsx` (or `.csv`), uploads stay as they are. Each person's documents are in their own folder
(`/share/household_docs/people/<name>/`), so they can also be opened over Samba, in the File editor, Excel or
LibreOffice, and they're in Home Assistant's backups when *Share* is ticked. The app's own database (`/data/docs.db`)
keeps only what files can't hold: who may see what, the search index, tags, activity and settings.

Same family as Calorie Tracker, Household Todo, Splitpot, Household Chat, Family Tree, Household Vault and Household
Arcade: ingress identity, `admin_users`, App settings in the app, themes, backups, notify targets
(`HA_ADDON_PATTERNS.md` §4). Shared code will come from the repository's `common/` folder (`SHARED_CODE_PLAN.md`,
option A).

**Why its own app and not part of Household Vault:** Vault is the hardened, encrypted app with no `/share` access and a
pending security review. Docs are plain files in `/share`, like Household Chat's. Keeping them apart keeps Vault's
promise ("everything here is encrypted") simple and its review small.

## 1. Purpose & scope

- **For a small household** (4–6 people), everyone signing in with their Home Assistant login.
- **Three document types:**
  - **Note** — plain text (`.txt`) or Markdown (`.md`, §17.2). Wrapping, a monospace toggle, tappable links, Find &
    Replace.
  - **Checklist** — items with tick boxes, one level of indent, reorder, *Hide ticked*, *Untick all*, who ticked what
    and when (`.md` task list).
  - **Sheet** — a grid with **formulas** (§8): sums, averages, min/max, counts, rounding, `IF`, `SUMIF`, lookups,
    arithmetic, references and ranges, number formats, a totals row, tabs, charts (`.xlsx`; `.csv` for plain ones).
- **Folders**, nested, in your own space (**My docs**, your folder under `/share`). Anything you own — a document or a
  whole folder — can be **shared** with chosen people or **Everyone**, as **Can view** or **Can edit** (§6).
- **Admin shared folders** (§9): an admin picks any other existing folder in `/share` (for example
  `/share/Documents/House`) and gives each person **read-only** or **read and write** access.
- **Any file** can live in a folder: uploads and camera scans (§17.7) are stored as they are; images preview, the rest
  download.
- **Search** (§10) over My docs, Shared with me, Everyone and the shared folders you have access to.
- **Trash** (30 days), **version history** per document (§7), **favourites**, **recently opened**.
- **Extras** (§17): tags and colours, Markdown notes, links between documents, activity feed and follows, PDF text
  search, optional AI, phone camera scan, storage report, HA sensors, more for sheets, Google Keep import, print / PDF,
  pins and Quick note.
- **Out of scope:**
  - Encryption. **Docs are plain files** readable by anyone with access to `/share` (§3.2). Passwords and card
    numbers belong in Household Vault; the editor says so.
  - Rich text, images inside documents, comments, real-time co-editing (two people at once are handled by small saves
    and conflict checks, §7).
  - `.docx` editing; pivot tables; macros.
  - Grocery lists, packing lists, chores and reminders with due dates (§1.1).

### 1.1 How it fits with the other apps
| Need | Where it lives | What Docs does |
|---|---|---|
| Grocery / shopping list | Receipt Price Intelligence (knows prices and stores) | Nothing. Docs checklists are for everything else (a party plan, a "before we leave the house" list). |
| Packing lists, chores, tasks with due dates, reminders, recurring lists | Household Todo | Nothing. Docs checklists have no due dates, assignees or reminders on purpose. |
| Passwords, card numbers, codes | Household Vault | Hint + "this looks like a password" (§3.3). |
| Files shared into a chat | Household Chat (shared folders in chats) | Admin shared folders work the same way; the same `/share` folder can be shared in both apps. Both check paths with the same shared `share_paths.py`. |
| Manuals, warranties, PDF bills | Docs (decided 2026-10-04: kept separate from Receipt Price Intelligence) | Stored as ordinary files in any folder — upload, preview, download, search by name and (from step 9) PDF text. No link to the Receipt app. |

## 2. Stack & file layout

- **Backend:** FastAPI + uvicorn on `python:3.12-alpine` (+ `tzdata`), port **8105**, the pinned dependency set shared
  by the siblings (`fastapi`, `uvicorn`, `python-multipart`), plus, pinned:
  - **`openpyxl`** (MIT) with **`defusedxml`** — reads and writes `.xlsx` sheets (§8.4); *(built)* pinned
    `openpyxl==3.1.5`, `et-xmlfile==2.0.0` (openpyxl's one dependency, pinned for a reproducible image) and
    `defusedxml==0.7.1` — all pure Python, nothing to compile on Alpine;
  - **`pypdf`** (BSD) — PDF text for search (§17.5), from step 9. *(built)* `pypdf==6.19.0`, pure Python with no
    dependencies; it runs only inside `formats/pdf_worker.py` (§17.5). No crypto library is installed, so PDFs
    encrypted with AES count as password-protected even when they open with an empty password (RC4 ones are read).
- **Storage:**
  - **Files:** every document, folder and upload under `/share` — the **docs folder** (`/share/household_docs` by
    default, §5.1) and the admin shared folders (§9).
  - **Database:** SQLite `/data/docs.db` (WAL): people, sharing, the file index, search (FTS5, `LIKE` fallback),
    checklist tick details, tags, activity, settings. No document text is the only copy in the database.
- **Frontend:** plain HTML/CSS/JS, no build step, no third-party code. `sheetcalc.js` (formula engine) and `md.js`
  (Markdown → DOM) are written for the app and tested with Node.
- **Shared code** from `common/` (byte-identical copies): `auth_core.py`, `whoami.py`, `db_core.py`,
  `settings_core.py`, `people_admin.py`, `web_security.py`, `backup_core.py`, `housekeeping.py`, `ha_client.py`,
  `ha_time.py`, `ha_notify.py`, `ha_people.py`; `ui.js`, `settings.js`, `people.js`, `backnav.js`, `whoami.js`,
  `http-warning.js`, `theme-boot.js`, `themes.css`, `settings.css`, `csv_export.py` (the search results' CSV,
  step 4); the test helpers; *(built, steps 8 and 11)* `sensor_publisher.py` and `ai_client.py` (with their shared tests);
  *(built, step 12)* `app_bus.py` + `ha_ws.py` (messages to Household Chat and Household Todo, with `fake_ha_bus.py` and
  `test_app_bus.py`) and `connected-apps.js` (Admin → App settings → Connected apps, `ConnectedApps.safePath`). The path checks are the app's own `store/paths.py`
  for now; they move to a shared `share_paths.py` when Household Chat uses the same module.

```
household_docs/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore  README.md  DOCS.md  CHANGELOG.md
├── icon.png  logo.png  translations/en.yaml  spec/SPEC.md  tests/
└── app/  main.py config.py auth.py db.py settings.py housekeeping.py ha_client.py notify.py
          sharing.py docops.py documents.py files.py share_folders.py
          common/ (copied from the repository's common/)
          store/ roots.py paths.py kinds.py nodes.py fileio.py index.py versions.py trash.py marker.py
          sheets.py                                       (step 5: open / save cells / export / import / convert)
          formats/ text.py checklist_md.py sheet_model.py sheet_xlsx.py sheet_csv.py xlsx_worker.py
          search/ fts.py parse.py pattern.py filters.py engine.py regex_worker.py saved.py
          routers/ common.py me.py nodes.py docs.py shares.py files.py search.py share_folders.py admin.py sheets.py
                                                          (+ activity.py, ai.py …)
          tags.py links.py pins.py docpdf.py pdfwriter.py pdf_fonts.py   (step 7)
          store/moving.py                                 (step 6: §5.7)
          routers/organise.py                             (step 7: tags, links, PDF, pins, Quick note)
          app_messages.py kids.py templates.py filing.py changes.py routers/connect.py routers/tidy.py   (step 12)
          static/ index.html textutil.js md.js app.js docs.js sheetcalc.js sheet.js files.js search.js organise.js
                  admin.js start.js style.css             (+ scan.js …; step 12: connect.js tidy.js)
```

**Code map (built in steps 1–4) and where later steps plug in:**

| Module | What it does | Extension point |
|---|---|---|
| `config.py` | env (`DATA_DIR`, `SHARE_DIR` — tests point both at temp folders), `/share` display ↔ real paths, time | — |
| `db.py` | §5.4 schema, `MIGRATIONS` (columns older databases lack), `POST_MIGRATE` hooks, `state_get/state_set` (install id, `docs_confirmed`), FTS5 with a LIKE fallback table | new tables go in `SCHEMA` (`CREATE … IF NOT EXISTS`), new columns in `MIGRATIONS` |
| `settings.py` | the App settings registry (§4) | one `Setting(...)` line per new setting |
| `auth.py` | identity, `require_user` (turned-off people → 404), `require_admin` | — |
| `store/roots.py` | the documents folder's state (checked at start-up, every 5 min, on choose), person roots, `root_real()` (realpath-checked every call), `hidden_dir(root, ".trash"/".versions"/".tmp")` | admin shared folders are `roots.kind = 'shared'`: `root_real` and `hidden_dir` already handle them |
| `store/marker.py` | §5.6 location checks, the marker, the `/share` folder browser | §5.7's copy check and switch build on `check()` |
| `store/paths.py` | name rules (Windows-safe), `resolve(root_real, rel)` (realpath inside the root), unique names | the candidate for `common/python/share_paths.py` when Chat adopts it |
| `store/kinds.py` | the node-kind registry: extensions, *document* or not, how a kind is detected and indexed; `stats` (columns kept for search filters: a checklist's open / ticked items) and the raster check for previews (`raster_type`, `nodes.preview`) | *(built, step 5)* `Kind("sheet", exts=("xlsx", "csv"))` with `info(path, size, limit) → (text, columns)` (kinds whose files aren't text: one call, cached per file version, gives the searchable text and `sheet_cells`) and `new_file(ext)` (what a new file holds: an empty app-owned .xlsx, or an empty .csv) |
| `store/nodes.py` | index rows: insert, `update_stat`, `relocate` (renames/moves keep ids), `delete_rows`, `node_json` | — |
| `store/fileio.py` | write-to-temp-then-rename, per-file lock, etags; `WRITE_GUARDS` | §5.7's read-only mode appends a guard |
| `store/index.py` | walk / one-folder scan / stat-on-open, inode + hash rename matching, `gone_at`, purge | new kinds are picked up through `kinds` |
| `store/versions.py`, `store/trash.py` | §7.3, §7.4 | — |
| `sharing.py` | roles, `access_cte()` (joined into every list and search query), `role_of()`, shares, Shared with me / Everyone | `access_cte` already grants `root_access` (shared folders) |
| `docops.py` | create / rename / move / copy / delete / transfer with the §6 permission rules; **places**: a folder id, `root:<id>` (an admin shared folder's top) or none (My docs top) — `place()`, `target_folder()`, `place_ref()` | — |
| `files.py` | uploads (streamed raw body, `upload_mb`, cleaned names, keep both / replace), image previews (magic bytes), streamed `.zip` downloads (safe names, caps) | the camera scan (step 9) saves through `prepare_upload` / `finish_upload` |
| `share_folders.py` | admin shared folders (§9): the checks, add / change / stop, the admin and per-person lists | — |
| `documents.py` | open/save notes, checklist operations, version restore | sheets open through `sheets.open_sheet`; restoring a sheet version re-indexes it through the kind |
| `sheets.py` *(step 5)* | open a sheet (an .xlsx through the worker), save changed cells with per-cell conflicts or the whole sheet, export, import, Convert to .xlsx, Save a copy to edit | — |
| `formats/sheet_model.py`, `sheet_xlsx.py`, `sheet_csv.py`, `xlsx_worker.py` *(step 5)* | the sheet as JSON and its checks (limits, tab names, formats, colour rules, charts), the functions the engine knows, number formats ↔ Excel's, search text; .xlsx writing (in the app) and reading (in the time-limited worker); .csv dialects | — |
| `search/parse.py`, `pattern.py`, `filters.py` | the box's syntax (§10.5); wildcards, path patterns, regex checks and literal text; dates (HA's time zone), sizes, type groups | — |
| `search/engine.py` | the Search page's query: access CTE + scopes + filters + words in one SQL query; regex candidates for the worker; results (location, highlights, open-at-line), grouping, paging | `engine.FILTERS[key] = fn(ctx, value, neg) → (sql, params)`: tags (`tag:`) in step 7 |
| `search/regex_worker.py` | regular expressions in a separate process, killed at 5 s, one per person | — |
| `search/saved.py` | saved (pinned) and recent searches | — |
| `static/app.js` | shell, router, sidebar / phone bar, spaces, folder view (list / grid, tick boxes, drag and drop), Settings | `Docs.route`, `Docs.addNew` (➕ New), `Docs.kind`, `Docs.addAction` (⋯ menus), `Docs.addSpace` (sidebar; `children()` for sub-links), `Docs.addBulk` (the selection bar) |
| `static/files.js` | uploads with progress, drop zones, the file panel (preview), the selection bar, 📁 Shared folders | `Docs.addBulk({id, label, show(items), run(items, sel)})` (tags add "Tag") |
| `static/search.js` | the Search page, the header box's hand-over, saved searches | — |
| `static/docs.js` | editors and item dialogs (share, move, transfer, history, where stored) | `Docs.editors[kind] = render(page, doc, ctx)` (ctx: `current`, `atLine`, `atCell {tab, ref}`); `Docs.editorHead(doc, ctx)` |
| `static/sheetcalc.js`, `static/sheet.js` *(step 5)* | the formula engine (Node-tested, no DOM) and the grid editor | — |
| `static/admin.js` | Admin tabs | `Admin.addTab({id, label, order, render})` |
| `static/textutil.js` | DOM-free helpers: the secret hint, the line compare, search-snippet marks, the Search page's address and chips, upload name clashes (Node-tested) | more pure helpers go here (or their own file, like `sheetcalc.js`) |
| `static/start.js` | starts the app after every script registered itself | stays the last script on the page |
| `store/moving.py` *(step 6)* | §5.7: read-only mode (a write guard + paused jobs), the new location's checks, the copy check (a background job), the switch | — |
| `tags.py`, `links.py`, `pins.py`, `docpdf.py` + `pdfwriter.py` *(step 7)* | §17.1 tags and colours, §17.3 `[[links]]` and backlinks, §17.13 pins and Quick note, §17.12 the PDF of a note or checklist | — |
| `static/md.js` *(step 7)* | Markdown → blocks → DOM nodes (Node-tested, fuzzed) | — |
| `static/organise.js` *(step 7)* | the tag dialog and 🏷 Tags, pins on the home page, ⚡ Quick note, print / PDF actions | — |
| `activity.py`, `follows.py` *(step 8)* | §17.4: `activity.record()` at every change (and from the index), the feed with the access CTE; follows and the batched notifications (`follows.dispatch`, housekeeping) | `activity.HOOKS`: f(conn, action, node, actor) after every record (HA sensors) |
| `ha_sensors.py` *(step 8)* | §17.9 on the shared `sensor_publisher`: the states, dirty items from Activity, the 15-second tick and 5-minute re-post, removal | — |
| `storage_report.py` *(step 8)* | §17.8: per-folder totals, types, History / Trash, growth (`storage_daily`), duplicates and Keep one, Admin → Storage, Empty all Trash | — |
| `pdftext.py` + `formats/pdf_worker.py` *(step 9)* | §17.5: the background job and the time-limited worker; derived text (PDF text, text read by AI) put back into `fts` | — |
| `imports.py` *(step 9)* | §17.11: receiving a .zip, its checks, the preview token, the Keep and .zip import jobs | — |
| `ai_client.py`, `ai_usage.py`, `ai.py` *(step 11)* | §17.6 on the shared `ai_client`: the provider calls (picture types fixed), usage rows and Admin → AI usage, the actions, the per-folder reading of scans | — |
| `wiring.py` *(steps 8–11)* | plugs the extras into the core: `routers/nodes.ROW_EXTRAS` / `ROOT_EXTRAS` (fields per item, per shared folder top), `documents.META_EXTRAS` (an open document), `routers/me.ME_EXTRAS` (/api/me), `activity.HOOKS` | the extension points later steps use too |
| `routers/activity.py`, `routers/bring.py`, `routers/ai.py` *(steps 8, 9, 11)* | the routes of §12's step-8–11 rows | — |
| `app_messages.py` + `routers/connect.py` *(step 12)* | §17.15–§17.16 on the shared `app_bus`: start / stop, the page path (`panel`), requests the page polls (`bus_requests`), the reply callbacks (inside the bus's transaction: they only write down) and `pump()` (after them: member shares' notifications, Move, the next part of a long checklist), Admin → Connected apps | `app_messages.status()` in `/api/me` → `apps` |
| `kids.py` *(step 12)* | §17.20: the routes a child can't use (`BLOCKED`, checked by `auth.require_user`), those checked by content (`CHECKED`) and the rest (`ALLOWED`) — every route must be in one (a test walks the route table) | a new route gets a line here |
| `templates.py`, `filing.py`, `changes.py` + `routers/tidy.py` *(step 12)* | §17.17 templates; §17.18–§17.19 filing and clean-up rules (the arrival queue, Undo, the daily job); §17.21 what changed (`saw()`, the dots, `changedSince`, `is:new`, Mark all as seen) | `filing.on_activity` and `changes.row_extras` are wired in `wiring.py` |
| `static/connect.js`, `static/tidy.js` *(step 12)* | ⋯ → Send to chat…, ⋯ → Make a Todo list… and the checklist's note; ➕ New → From a template…, ⋯ → Save as template…, ⋯ → Filing and clean-up rules… (and on a shared folder's top), the rules' note on the folder, ✨ Suggest a name and folder, "Changed since you last looked" | `Docs.sendToChat`, `Docs.todoDialog`, `Docs.templateDialog`, `Docs.rulesDialog`, `Docs.waitForRequest`, `Docs.noAccessPage`, `Docs.isChild` |
| `static/activity.js`, `static/bring.js`, `static/scanpdf.js`, `static/ai.js` *(steps 8, 9, 11)* | 🕑 Activity, Follow, Show in Home Assistant, 💾 Storage and Admin → Storage; 📷 Scan, imports, the file panel's text; the scan's arithmetic and JPEG-in-PDF writer (Node-tested, no DOM); the ✨ AI actions and Admin → AI usage | core hooks they use: `Docs.settingsRows`, `Docs.rootActions`, `Docs.headExtras`, `Docs.filePanelExtras`, `Admin.settingsFields` / `settingsGroups` / `settingsHooks` |

## 3. Manifest, options, security

### 3.1 Manifest
```yaml
name: "Household Docs"
version: "1.0.1"
slug: household_docs
url: https://github.com/sameerkotra/ha-apps
arch: [amd64, aarch64, armv7]
startup: application
boot: auto
ingress: true
ingress_port: 8105
panel_icon: mdi:file-document-multiple
panel_title: Docs
panel_admin: false
homeassistant_api: true   # person.* (the user list), phones, notify, sensors, HA's time zone and currency
hassio_api: false
auth_api: false
docker_api: false
full_access: false
apparmor: true
map:
  - type: share
    read_only: false
options:
  admin_users: []
schema:
  admin_users: ["str"]
```
- Ingress only, no `ports:`. `admin_users` is the only option; with it empty, every page shows the "No admin yet"
  banner and nobody is promoted.

### 3.2 People, access and who else can read the files
- People come from HA Persons (`ha_people.py`), synced at start-up and every 5 minutes. **New people get access by
  default** (`new_people_access`); an admin can turn anyone off. Turned-off people see "An admin has turned off
  Household Docs for you"; their folder and files stay, and stay shared.
  *(built)* Every content route answers a turned-off person **404** (detail "An admin has turned off Household Docs
  for you"); `/api/me` and `/api/whoami` still answer, so the page says why. Sharing with a turned-off person is
  refused (409). People from Home Assistant (persons with a login) are added to `users` at start-up and every 5
  minutes, so they can be shared with before their first visit; their folder is made on that first visit.
- **Through the app**, a document is visible only to its owner, people it (or a folder above it) is shared with,
  Everyone shares, and — in admin shared folders — people with access to that folder. Everyone else gets **404**.
  Admins get no content access as admins **through the app's pages**. *(security review 2026-10)* What that does and doesn't mean:
  an admin can't name themselves a child's parent (§17.20) — though, by design, an admin may name anyone else, the
  person is notified and it's audited — a parent's view covers only what a child made while a child, Admin → Storage shows counts within each folder only (§17.8), and the folder browser doesn't list inside
  the documents folder or Chat's files folder (§5.6). But whoever administers Home Assistant runs the host: they can
  read `/share` directly, download a backup with `backup_files`, restore a database of their own making, and the
  copy check's report lists every file's path (§5.7). Those are inherent; DOCS.md says so plainly ("admins can't
  read documents through the app's pages, but anyone who administers Home Assistant can read the files on disk
  and in backups") rather than claiming more. Backup downloads, restores, copy checks and the report download are
  admin-only and in the audit log.
- **Outside the app, the files are ordinary files in `/share`.** Anyone who can reach `/share` can read and change
  **everyone's** documents: Samba/NFS users, the File editor, other apps with `share` access (Household Chat, Family
  Tree, …), and anyone holding an HA backup that includes Share. The app's sharing rules don't apply there.
  - DOCS.md, the first-run screen and Admin say so plainly.
  - DOCS.md shows how to keep the docs folder off a Samba share (or on its own share with its own password).
  - An admin chooses where the folder is (Admin → Documents folder, §5.6) and can move it later (§5.7), e.g. to
    network storage or a share with its own password.

### 3.3 The page (ingress pages share HA's origin)
- CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self';
  object-src 'none'; base-uri 'none'; frame-ancestors 'self'`.
- Document text, titles, checklist items, sheet cells and file names are set with `textContent`; **nothing
  user-written is ever parsed as HTML** (Markdown is turned into DOM nodes by `md.js`, §17.2).
- **Serving files:** downloads carry `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy: sandbox`. Inline previews only for PNG, JPEG, GIF and WebP, checked by magic bytes.
  **Never inline:** SVG, HTML, XML, PDF, JS.
- *(built)* Whether a file is a previewable image is decided from its first 16 bytes when it's indexed (`nodes.preview`
  holds the type, whatever the name says — an SVG called `.png` gets none, a PNG called `.bin` gets one); the
  preview route reads the file (at most 25 MB, opened without following links), checks the bytes again and sends
  exactly those bytes (`Content-Disposition: inline`, `nosniff`, `CSP: sandbox`), so a file swapped in between is
  never served. Folder and multi-item `.zip` downloads carry the same download headers.
- *(security review 2026-10)* **Opening files after the path check** (`store/fileio.open_fd` / `open_read`): every read of a document,
  a download, a version download, a preview, a `.zip` member, a snippet or a PDF's text opens the file once with
  `O_NOFOLLOW` and then checks where the descriptor really is (`/proc/self/fd`) — inside `/share` — so a folder on
  the way swapped for a link (over Samba) between the check and the open can't make the app read `/data`.
  Downloads are streamed from that descriptor (never re-opened by path). Writes (`write_atomic`) open the target
  folder the same way and make the final link / rename relative to its descriptor (`dst_dir_fd`); a version (§7.3) is copied from the
  descriptor the file was opened and hashed with, into the version folder through its descriptor (re-review
  2026-10); `.tmp`,
  `.versions` and `.trash` that are links are refused.
- *(security review 2026-10)* **Request sizes**: every route that doesn't stream its body is limited while the body is read (an ASGI
  wrapper counting bytes, `main.BodyLimit`) — not only by `Content-Length` — so a chunked body can't get past the
  limit (256 KB; 27 MB for `/api/docs…`; 12 MB for `/api/ai/…`). Streamed routes (uploads, scans, imports,
  `/api/import`, restore) count their own bytes against their own limits (`upload_mb`, `max_doc_mb` …).
- A hint under the editor: "Docs are plain files — keep passwords and card numbers in Household Vault." A light check
  in the browser (nothing sent) notices things like `password:`, a card number or an `otpauth://` URI and says so.

### 3.4 Risks
| # | Risk | Mitigation / residual |
|---|---|---|
| 1 | Everyone's documents are readable and changeable by anyone with `/share` access (Samba, other apps, backups). | Said plainly in DOCS, first run and Admin; DOCS shows how to keep the folder off Samba; an admin can choose or move the location (§5.6–5.7). **Residual:** the app can't protect files outside itself. |
| 2 | Files changed, renamed or deleted outside the app. | The index notices (scan + check on open, §5.5); renames are followed by inode — within the same root only, and only when plausible (§5.5); conflicts get *Keep mine / Use theirs*; versions keep the app's last copy. **Residual:** a rename outside the app on another file system, or a move into another root, loses shares, tags and history for that file (on purpose: they never cross owners). |
| 3 | A `/share` file with script (HTML/SVG) opened on HA's origin. | Never inline; `attachment` + `nosniff` + `CSP: sandbox`; previews only for raster images checked by magic bytes. |
| 4 | Path tricks (`..`, symlinks, a folder swapped for a symlink). | Every path resolved and `realpath`-checked under its root on every request; hidden entries and symlinks out skipped. |
| 5 | Malicious `.xlsx` (zip bombs, XML entities). | `defusedxml`; size and unzipped-size limits; cell/row caps; parsing in a worker with a time limit. |
| 6 | Saving an `.xlsx` made elsewhere could drop features the app doesn't know (macros, pivots, images). | Such files open **read-only** with *Save a copy to edit* (§8.4). |
| 7 | A slow regular expression (ReDoS). | Regex search in a separate worker process, killed after 5 s (§10.4). |
| 8 | Search leaking what you can't open. | Access check inside every search query; a test scans every response. |

## 4. App settings (Admin → App settings; `app_settings` table; apply without a restart)

| Setting | Default | Meaning |
|---|---|---|
| `docs_path` | `/share/household_docs` | The docs folder (§5.1), set by an admin in **Admin → Documents folder** (§5.6), not in the settings list. Any folder inside `/share` (not `/share` itself), including network storage mounted there (`/share/<mount>/…`). Changing it later: the admin copies the files, the app checks the copy and switches (§5.7). |
| `folder_names` | `display` | Each person's folder name: `display` (Home Assistant display name, "Priya") or `username`. Only used when a folder is first made. |
| `new_people_access` | `true` | New HA people get access automatically. |
| `everyone_shares` | `true` | People may share with *Everyone*. |
| `everyone_default_role` | `viewer` | Role picked first when sharing with Everyone. |
| `versions_kept` | `30` | Versions kept per document (5–200). |
| `trash_days` | `30` | Days before Trash is emptied (1–365). |
| `max_doc_mb` | `5` | Largest note / checklist / sheet the editor opens (1–25 MB). Bigger files download. |
| `upload_mb` | `100` | Largest upload (1–2048). |
| `quota_gb` | `0` | Per-person limit for their own folder (0 = none). |
| `scan_minutes` | `10` | How often the index re-walks the folders (2–120). |
| `content_index_mb` | `1` | Largest text file whose content is indexed (0 = names only, up to 20). |
| `regex_search` | `true` | Allow regular-expression search (§10.4). |
| `secret_hint` | `true` | The "this looks like a password" hint (§3.3). |
| `backup_files` | `false` | Admin backup zip also includes the docs folder's files (can be large). Off: the zip holds the database only; files are in HA's backups when *Share* is ticked. |

Extras add more (§17.14). *(built, steps 8–11)* `pdf_index_mb` (20, 0–64: the index hashes files up to 64 MB, and a PDF's
text is matched to its content by SHA-256), `activity_days` (90, 7–730), `ha_sensors` (on), `keep_import` (on) — in a
group "Activity, Home Assistant and imports" — and the group **AI**: `ai_enabled` (off), `ai_provider` (ollama /
openai / anthropic), `ai_url` (empty: the provider's usual address; Ollama needs one), `ai_api_key` (secret),
`ai_model` (text), `ai_vision_model` (empty: the text model), `ai_monthly_tokens` (0 = no limit; *decision*: the
limit counts tokens, input + output, in Home Assistant's calendar month — what a provider charges by),
`ai_price_in` / `ai_price_out` (cost estimate only, as in the other AI apps). Personal settings added:
`notifyFollows` (on), `quietFrom` / `quietTo` ("22:00" / "07:00"), `showAi` (on), `scanName` ("Scan {date} {time}").
*(built)* Every setting above is there (`upload_mb` with step 3, `regex_search` with step 4);
`docs_path` is a hidden setting, changed only through Admin → Documents folder. **Each person's own settings** (Settings): theme, notify me when something is shared with me,
number style for sheets, folders first, default search scope, date display, default note type, new sheets as `.xlsx`
or `.csv`. *(built, step 5)* New sheets as `.xlsx` or `.csv` is `newSheets` (`xlsx` by default; Settings → You); it is
used by ➕ New → Sheet and for importing a `.csv`. *(built, steps 6–7)* `numberStyle`: `auto` (the device's own, as
before), `en` 1,234.56, `de` 1.234,56, `in` 12,34,567.89 — the sheet editor shows Number / Currency / Percent (and
General with a decimal comma for `de`) in that style and reads typed numbers in it (`de`: `1.234,5`; a plain `1.5`
that can't be thousands is still 1.5; `in`: lakh grouping; dates and formulas are the same in every style; the cell
editor shows a `de` number with a decimal comma). `defaultNote`: `note` or `markdown` — what ➕ New → Note makes
(the other kind is listed too). Not built: "folders first" (always on), default search scope, date display.

## 5. Files on disk and the index

### 5.1 The docs folder (default `/share/household_docs/`)
```
/share/household_docs/
  .household_docs              marker: install id + format version (§5.6)
  people/
    Priya/                     Priya's My docs — her folders and files as she made them
      Inbox/                   Quick notes (§17.13)
      Trip 2026/
        Packing ideas.md       checklist
        Budget.xlsx            sheet
        Hotel booking.pdf      upload
    Alex/ …
  .trash/<person-folder>/<id>/ deleted items with their original path (`.item.json` beside them)
  .versions/<node-id>/<n>.<ext> earlier versions of documents
  .tmp/                        write-then-rename scratch (same file system)
```
- **Each person's folder** is made the first time they open the app: name from `folder_names`, cleaned for Windows
  (`/ \ : * ? " < > |`, trailing dots/spaces, reserved names); a clash adds the user name ("Priya (priya2)"). The
  mapping person → folder is in `users.folder`; renaming someone in HA **doesn't** rename the folder (an admin can, in
  Admin → People).
- **Hidden folders** (`.trash`, `.versions`, `.tmp`, the marker) are never listed in the app.
- **What's where:** a person's own documents live only in their folder. Sharing never copies files; things shared with
  you are opened from the owner's folder.

### 5.2 File formats
| Type | File | Notes |
|---|---|---|
| Note | `.txt` | UTF-8 (a BOM is kept if the file had one); line endings kept as found (new files: `\n`). |
| Markdown note | `.md` (not a checklist) | §17.2 |
| Checklist | `.md` where every non-blank line is `- [ ] …` / `- [x] …` (2 spaces = one indent level) | Opens in any editor as a task list. Who ticked and when is kept in the database (§5.4), matched by line. |
| Sheet | `.xlsx` (default) or `.csv` | `.xlsx` keeps formulas, formats, widths, frozen panes, tabs, conditional colours and charts (§8.4). `.csv` = one tab, values and `=` formulas only. |
| Folder | a directory | |
| Anything else | as uploaded | Listed, previewed (raster images), downloaded. |
- **Titles are file names** (without the extension). Renaming a document renames the file. Names Windows can't hold are
  refused with a reason.
- A `.md` that stops being a pure task list (edited outside) opens as a Markdown note, and back again when it is one.

### 5.3 Roots
Two kinds of root, handled by the same code (`store/roots.py`):
- **People folders** under the docs folder — owner = that person; sharing per item (§6).
- **Admin shared folders** — no owner; access per person per folder (§9).
Every node belongs to exactly one root; every path a request names is `root + relative path`, checked by
`share_paths.py` (§9.3).

### 5.4 Database (`/data/docs.db`)
```sql
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT, ha_person TEXT,
  folder TEXT UNIQUE,                                     -- folder name under people/ (NULL until first visit)
  disabled INTEGER NOT NULL DEFAULT 0, prefs TEXT, last_seen TEXT, created_at TEXT NOT NULL);

CREATE TABLE roots (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('person','shared')),
  user_id TEXT REFERENCES users(id),                      -- kind = person
  path TEXT NOT NULL UNIQUE,                              -- relative to /share
  label TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
  last_scan_at TEXT, last_scan_ms INTEGER, missing INTEGER NOT NULL DEFAULT 0);

CREATE TABLE nodes (                                      -- the index: one row per file and folder in every root
  id TEXT PRIMARY KEY,                                    -- stable id: shares, tags, links, versions point here
  root_id TEXT NOT NULL REFERENCES roots(id) ON DELETE CASCADE,
  rel TEXT NOT NULL,                                      -- path inside the root, '/'-separated
  parent_id TEXT REFERENCES nodes(id),
  name TEXT NOT NULL, name_folded TEXT NOT NULL, ext TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('folder','note','markdown','checklist','sheet','file')),
  size INTEGER, mtime TEXT, ctime TEXT, inode INTEGER, dev INTEGER, sha256 TEXT,
  created_by TEXT, updated_by TEXT,                       -- users.id when done through the app; NULL = outside the app
  color TEXT, content_indexed INTEGER NOT NULL DEFAULT 0,
  gone_at TEXT,                                           -- not found on disk (kept 30 days so a rename can be matched)
  UNIQUE (root_id, rel));
CREATE INDEX idx_nodes_parent ON nodes(parent_id);
CREATE INDEX idx_nodes_name ON nodes(name_folded);
CREATE INDEX idx_nodes_mtime ON nodes(mtime);
CREATE INDEX idx_nodes_ext ON nodes(ext);
CREATE INDEX idx_nodes_inode ON nodes(dev, inode);

CREATE TABLE shares (node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,                                  -- users.id or '*' (Everyone)
  role TEXT NOT NULL CHECK (role IN ('manager','editor','viewer')),
  viewers_tick INTEGER NOT NULL DEFAULT 1, hidden INTEGER NOT NULL DEFAULT 0,
  added_by TEXT, added_at TEXT NOT NULL, PRIMARY KEY (node_id, user_id));

CREATE TABLE root_access (root_id TEXT NOT NULL REFERENCES roots(id) ON DELETE CASCADE,   -- admin shared folders
  user_id TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('ro','rw')), PRIMARY KEY (root_id, user_id));

CREATE TABLE versions (node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, n INTEGER NOT NULL,
  file TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  made_by TEXT,                                           -- NULL = changed outside the app
  created_at TEXT NOT NULL, PRIMARY KEY (node_id, n));

CREATE TABLE trash (id TEXT PRIMARY KEY, node_id TEXT, root_id TEXT NOT NULL, original_rel TEXT NOT NULL,
  trash_rel TEXT NOT NULL, owner_id TEXT, deleted_by TEXT, deleted_at TEXT NOT NULL);

CREATE TABLE checklist_ticks (node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  item_key TEXT NOT NULL,                                 -- hash of the item text + occurrence number
  done_by TEXT, done_at TEXT, PRIMARY KEY (node_id, item_key));

CREATE TABLE user_state (user_id TEXT NOT NULL, node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  favourite INTEGER NOT NULL DEFAULT 0, pinned_order INTEGER, opened_at TEXT, PRIMARY KEY (user_id, node_id));

CREATE VIRTUAL TABLE fts USING fts5(node_id UNINDEXED, name, body, tokenize='unicode61 remove_diacritics 2');

CREATE TABLE saved_searches (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, query TEXT NOT NULL,
  filters TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE recent_searches (user_id TEXT NOT NULL, query TEXT NOT NULL, filters TEXT NOT NULL, used_at TEXT NOT NULL,
  PRIMARY KEY (user_id, query, filters));
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE TABLE user_notify (user_id TEXT NOT NULL, service TEXT NOT NULL, PRIMARY KEY (user_id, service));
CREATE TABLE audit_log (id TEXT PRIMARY KEY, actor_id TEXT, node_id TEXT, root_id TEXT, action TEXT NOT NULL,
  created_at TEXT NOT NULL);                              -- no content, ever
```
Extras add their own tables (§17). Missing columns are added at start-up and after a restore (`db.MIGRATIONS`).

*(built)* Differences from the schema above: `nodes.mtime_ns` (exact change detection and etags) and
`nodes.trash_id` (a node in Trash keeps its row — shares, ticks, versions — with `rel` pointing at
`.trash/<trash id>/…`, never a real path); `versions.saved_by` (whose save kept the copy: the 5-minute rule);
`user_state.hidden` (Hide is per person, for direct and Everyone shares alike, so `shares.hidden` isn't used and
isn't created); a unique index on the person root per user. `saved_searches` / `recent_searches` are as above (step 4).
Step 3–4 columns (in `db.MIGRATIONS`): `nodes.check_open` / `nodes.check_done` (a checklist's open and ticked
items, for `is:open` / `is:done`), `nodes.preview` (the raster image type found in the file's first bytes, §3.3);
indexes on `nodes(sha256)` (duplicates) and `root_access(user_id)`.
Step 5 adds `nodes.sheet_cells` (JSON: for each line of a sheet's indexed text, the cell it came from,
`"Tab\tC14"`), so a search result opens the sheet at that cell without reading the file again.
Without FTS5, `fts` is a plain table searched with `LIKE`. Settings rows that aren't App settings hold this
install's id (`install_id`) and `docs_confirmed`.
Steps 8–11 add `activity`, `follows`, `ha_sensors`, `storage_daily`, `pdf_text`, `ocr_text`, `ai_folders`, `ai_calls`,
`keep_imports` (their columns are in `db.py`, described in §17.4–§17.11) and `nodes.pdf_pages`. Step 12 adds the bus's
`bus_outbox`, `bus_seen`, `bus_apps` (APP_MESSAGES_SPEC §4, made by `app_bus.migrate` in `POST_MIGRATE`),
`bus_requests`, `todo_sends`, `filing_rules`, `filing_queue`, `filing_log`, `cleanup_rules`, `kid_parents`, and the
columns `users.is_child`, `users.seen_from`, `users.child_since` (security review 2026-10, §17.20), `roots.kids`, `user_state.seen_at`, `user_state.seen_sha` (§17.15–§17.21).

### 5.5 Keeping the index right (`store/index.py`)
- **Walk** every root every `scan_minutes`, after every write through the app, on *Rescan now*, and for one folder
  when it's opened (a cheap `scandir` of that folder only). Unchanged entries (same size + mtime + inode) aren't
  re-read. Depth ≤ 10, 50 000 entries per root (Admin warns past that); the walk yields between batches.
- **New file** → new node (`created_by` NULL = "added outside the app"). **Changed outside** (size/mtime/hash differ)
  → content re-indexed, `updated_by` NULL, the previous app-saved copy is already in `.versions` (§7.3).
- **Renamed or moved outside** → matched by `(dev, inode)` (and size + hash when the inode changed), so the node keeps
  its id, shares, tags, links and history. No match within 30 days → the node is dropped.
- **Deleted outside** → `gone_at` set; after 30 days without a match, the node and its shares go.
- **Opening a document** always `stat`s the file first; if it changed since the index, the editor loads the disk
  version.
- *(built)* Symbolic links are skipped altogether (not only those pointing out of the root), so a walk can never
  loop or leave its root. A file matched by inode must also have the same size or the same name (a reused inode
  isn't mistaken for a renamed file). A one-folder scan also finds a file moved there from a folder not scanned
  yet: an indexed node with the same inode whose own path no longer exists is taken over. A walk that hits the
  50 000-entry cap marks nothing gone.
- *(security review 2026-10)* **Matching is narrow** (`index._match`): only nodes of the **same root** are candidates (a file that turns up
  in another person's folder or in an admin shared folder is always new there — shares, versions, tags, ticks and
  activity never cross owners or roots; History of an admin shared folder never ends up pointing from a person's
  folder). The candidate must be missing from its place now, or marked gone within the last 24 hours
  (`MATCH_HOURS`; inodes get reused). By inode: a **folder** must have the same name, the same modification time,
  or still hold an entry the index knows inside it (same name and inode); a **file** the same modification time
  and size, or the same SHA-256. By content when the inode changed (copied, then deleted): the same SHA-256 and
  size **and** the same name or the same folder.
- *(security review 2026-10)* **Other apps' stores are left out**: the walk, one-folder scans, folder `.zip`s and every file access
  (`roots.check_unmarked`) skip a folder holding `.household_chat_store` (Household Chat's files folder) or
  `.household_docs` (another install's documents folder) and everything inside it; nodes already indexed there go
  gone on the next scan and can't be opened meanwhile.

### 5.6 Choosing the documents folder (Admin → Documents folder)
- **First run.** Until an admin confirms a location, admins see a setup card on the home page: "Where should documents
  be kept?" with the default (`/share/household_docs`) filled in, a **folder browser** over `/share` (also shows
  mounted network storage), and *Use this folder*. Everyone else can already use the app with the default; the card
  only makes the choice visible. Confirming the default is one tap.
- **The page shows:** the current location, its status (OK / not found / read-only), free space on that storage, total
  size and file count, and the previous location if there was one (§5.7).
- **Checks on any location** (`store/marker.py`), on choosing it, at start-up and every 5 minutes:
  - **new / empty** → used; the marker `.household_docs` (this install's id + format version) and `people/` are
    created;
  - **ours** (marker matches this install) → used;
  - refused, with the reason: `/share` itself; **another install's** marker; **inside an admin shared folder or
    containing one**; **inside Household Chat's files folder** (found by its marker); **read-only**; **parent missing**
    (storage not mounted); a folder that already has files but **no marker** (offered only through the switch in §5.7,
    so files are never mixed up by accident).
- **The app never copies, moves or deletes the documents folder as a whole.** It writes only files people save, the
  marker, and its hidden folders.
- *(built)* Until an admin confirms a location, a usable default is set up automatically at start-up. **Choose**
  (`POST /api/admin/docs-folder/choose`) confirms the current folder, or switches to another one **only while no
  document exists yet** (afterwards moving is §5.7's job); the person roots are re-pointed and the old folder is
  left as it is. `POST …/inspect {path}` runs the checks without changing anything (the page shows the verdict as
  you type or browse). A confirmed folder that disappears is never re-created: it is "not found" until it's back.
  The marker is JSON: `{"install", "format", "app", "created"}`.
- If the folder disappears while running (network storage dropped), every page shows "Documents folder not found —
  is the storage connected?", nothing is written and the index isn't touched; it carries on when the folder is back.

- *(security review 2026-10)* The folder browser (`GET /api/admin/share-dirs`, `marker.browse`) lists nothing inside a documents folder
  or Household Chat's files folder (`sealed: "docs" | "chat"` with a note): an admin choosing a folder doesn't get a
  view of people's folder names.

### 5.7 Moving to a new location (the admin copies, the app checks and switches)
1. **Admin → Documents folder → Change location…** → pick the new folder (folder browser or a typed path). The app
   checks it can be used (§5.6 rules; it may be empty or already hold the copy).
2. **Pause changes (recommended):** a switch that puts the app in **read-only mode** for everyone ("Documents are
   being moved — you can read but not change anything until <admin> finishes"), so nothing changes after the copy.
   Scheduled jobs that write (trash clean-up, version pruning) pause too.
3. **The admin copies the files themselves** — the whole folder **including the hidden ones** (`.household_docs`,
   `.trash`, `.versions`). The page shows how, with the exact paths filled in:
   - with the **Samba** app from a computer (turn on *Show hidden files* first);
   - with the **Terminal & SSH** app: `rsync -a "/share/household_docs/" "/share/nas/household_docs/"` (or
     `cp -a`);
   - with the **File editor** / a NAS tool for network storage.
4. **Check copy** — the app compares the current folder and the new one and shows a report:
   - file and folder counts and total size on both sides;
   - **missing** in the new location, **different** (size or modified time; *Deep check* compares SHA-256 too, slower,
     with progress), **extra** files only in the new location;
   - hidden folders and the marker present or not (a missing marker is fine — the app writes it at the switch; missing
     `.versions` / `.trash` means history and Trash won't come along, said in plain words);
   - free space left on the new storage.
   Lists are capped at 200 lines each with *Download full report* (CSV).
5. **Switch** — allowed when the check shows nothing missing or different; otherwise only with *Switch anyway* and a
   typed confirmation that names how many files are missing. The switch:
   - writes the marker in the new folder (this install's id), and updates the old folder's marker with `moved_to` and
     the date (the only change made to the old folder);
   - changes `docs_path` and every person root's path in one database transaction;
   - **keeps every node id** by matching the same relative paths (`people/Priya/Trip 2026/Budget.xlsx`), so shares,
     tags, links, ticks, favourites and history carry over; then re-indexes the new folder (new files found, missing
     ones marked gone as in §5.5);
   - turns read-only mode off and tells everyone "Documents moved to the new location".
6. **The old folder is left exactly as it is.** Admin shows "Previous location: /share/household_docs — no longer used;
   delete it yourself when you're happy". **Switch back** is offered while it still exists (the same check and switch,
   so changes made since are caught as *different*).
- Every step is in `audit_log` (`docs_location_checked`, `docs_location_switched`, `read_only_on/off`).
- Admin shared folders aren't affected; the overlap rules (§9.1) are checked again against the new location.
- *(built, step 6 — `store/moving.py`)*
  - **Read-only mode** is `app_settings.read_only` `{on, by, byName, since, target}` (kept across restarts). Its guard
    is the first of `fileio.WRITE_GUARDS`, so every write through the app answers **423** "Documents are being
    moved — you can read but not change anything until <admin> finishes" — in admin shared folders too
    (*decision*: one rule, "you can't change anything", is easier to understand), plus Trash restore / empty, an
    admin's *Delete their documents*, *Rename folder*, the first-run *choose* and a backup restore. Paused: Trash
    clean-up, version pruning, temp-file clean-up, the hourly purge, making a new person's folder (made on their
    next visit after), making missing hidden folders, and the documents-folder set-up in `roots.check`. The index
    scan keeps running (it only reads the folders). Documents open with `canEdit`/`canTick` false and
    `readOnlyMode: true`; `/api/me` has `readOnly {byName, since}` for the banner on every page.
  - **The new location** (`POST …/docs-folder/target {path}`): §5.6's checks, and not the current folder, inside it
    or around it; usable when new, empty, holding files without a marker, or ours (a copy with the marker). The
    answer carries the copy instructions' paths: `rsync -a "<from>/" "<to>/"`, `mkdir -p … && cp -a "<from>/." …`,
    and the Samba paths `\\homeassistant\share\…`.
  - **The check** walks both folders (everything — hidden folders included — except `.tmp` and the marker, no
    links, ≤ 1 000 000 entries) in a background thread, one at a time; the page polls `GET …/check/{job}`
    (`phase` listing / comparing / hashing / done / failed, counts, `progress` while hashing). *Decision* on
    "different": quick — a different size, or modified times more than 2 s apart (FAT and SMB keep 2 s);
    deep — a different size or SHA-256 (times are ignored, so a copy that didn't keep them is fine when the
    contents match). The result: totals per side, the missing / different / extra lists (capped at 200 in the
    answer, all in `report.csv`), `people` / `.trash` / `.versions` on both sides, the marker (there, ours),
    free space and the space still needed. Jobs live in memory (the last five).
  - **The switch** needs that job, finished, for the same path and the same current folder; with anything
    missing or different it needs `force` and `confirm` = that number typed. It writes the marker (and
    `people/`, `.trash`, `.versions`, `.tmp`) in the new folder, adds `moved_to` / `moved_at` to the old marker,
    then in one transaction sets `docs_path`, every person root's path, `docs_previous {path, movedAt, movedTo,
    by}`, `docs_confirmed`, and read-only off; then a full scan (rows are matched by relative path, so every
    node id stays) and a phone notification to everyone with access. *Switch back* is the same check and switch
    towards `docs_previous` while it exists (its marker is ours, so it's usable; the switch drops `moved_to`).

## 6. Folders, ownership and sharing

### 6.1 Spaces (the sidebar)
- **📄 My docs** — your folder, as a tree.
- **👥 Shared with me** — documents and folders others shared with you, each showing its owner.
- **👪 Everyone** — items shared with Everyone.
- **📁 Shared folders** — the admin's folders you have access to (§9), each with 🔒 (read only) or ✏️.
- **⭐ Favourites · 🔎 Saved searches (pinned).**
- **🕑 Activity** — one page with tabs **Recent · Activity · Trash · Storage** (`#/recent`, `#/activity`, `#/trash`,
  `#/storage` stay as addresses; the sidebar shows Activity for all four). Home shows pins, favourites and My docs — not Recent.

### 6.2 Ownership
- In people folders the **owner is the person whose folder it is**. Things an editor creates inside someone's shared
  folder are written into that person's folder, so they're owned by the folder's owner; `created_by` records who made
  them.
- **Transfer ownership** moves the item (file or folder) into the new owner's folder (top level, or a folder they
  pick); shares, tags, links and history move with it.
  *(built)* Top level only (the giver can't see the new owner's folders), with "(2)" on a name clash; the previous
  owner keeps **Can edit** on the item (and can leave it); the new owner's own share on it is dropped (they own it)
  and they get a notification ("Alex gave you 'Car'").
- An admin can **delete a person's docs** (Admin → People: count and size, then a typed confirmation) — moves their
  folder to `.trash`.

### 6.3 Sharing
- **Share…** on anything you own: pick people and/or **Everyone**, each **Can view** or **Can edit**; the owner can also
  make someone a **Manager** (may share further and remove people).
- **Folder shares are inherited** by everything inside, now and later — including files added outside the app.
  A deeper share can add access but never take inherited access away. **Effective role** = highest of the item's own
  and every ancestor's shares.
- **Viewers** open, search, copy text, download, and *Make a copy* into My docs. Checklists have *Viewers may tick
  items* per share (default on).
- **Editors** edit, upload, create inside, rename, move **within** the shared tree, and delete (to Trash).
- **Owner/manager only:** moving out of the shared tree, sharing, deleting a shared folder.
- **Leave / Hide** removes something from your view (Everyone shares can be hidden, not left).
- No public links; everything stays behind HA's login.
- *(built)* Details: an editor may delete only items that carry no shares themselves (nor anything inside them);
  "the shared tree" for an editor's move is the highest item whose own share gives them Can edit, and they can't
  move that item itself; a manager may move anything within the owner's folder; Everyone can be given Can view or
  Can edit, never Manager; managers can't change or remove other managers; removing your own share is Leave.
  Moving between two people's folders is refused (that's Transfer, or Make a copy).
- *(security review 2026-10)* **What a viewer learns about the folders above**: an item's `path` (`GET /api/nodes/{id}`,
  `/api/docs/{id}`) is the full `/share/…` path for its owner and in admin shared folders; for anyone else only the
  folders they can open themselves, the rest as "…" ("… / Trip / Letter.txt"; `sharing.visible_path`). Share…'s
  inherited list (`shares_json`) names only folders above that the viewer can open, and only their shares.
  The same goes for every other place that names the folders above (re-review 2026-10): search results'
  `location` / `locationParts` and the CSV export, 🕑 Activity's `location`, grouping by location, and the
  storage report's lists all come from `search.engine.location()`, which for someone else's folder gives only
  the folders the viewer can open, the rest as "…" (`["…", "Evidence"]`, or `["…"]`); a `path:` filter on someone
  else's items is checked again against that visible part (a hidden folder's name never decides a result); follow
  notifications' folder labels likewise (§17.4).

## 7. Saving, conflicts, versions, trash

### 7.1 Writing files (`store/fileio.py`)
- Every write: to `.tmp/` on the same file system → `fsync` → `os.replace` onto the target. A per-file lock in the app
  serialises writes from different people. File mode `0664`.
- **Etag** = size + mtime_ns + SHA-256. Every save sends the etag it started from.

### 7.2 Conflicts
- **Notes:** a stale etag answers **409** with the newer text: *Keep mine* (saved; theirs becomes a version) / *Use
  theirs* / *Compare* (line diff). The same happens when the file was changed outside the app.
- **Sheets:** the browser sends changed cells; the server re-reads the file, applies them if those cells didn't change
  since, and writes. Only the same cell conflicts. A file changed outside the app while open reloads with a notice.
  *(built)* `PATCH /api/docs/{id}` `{etag, cells: [{tab, ref, was, now}], tabs?: [{name, cols, freeze, totals, cond,
  charts}]}`: `was` is the cell (value or formula and format) as the browser last heard it from the server, `now`
  the new one (formulas carry the value the browser computed, `c`). When the etag is current everything is applied;
  otherwise each cell is applied only if what's in the file is still `was` (or already `now`) — the others come back
  as 409 `{conflicts: [{tab, ref, theirs}], sheet, etag}` **while every other change in the request is saved**
  (*Keep mine* sends the cell again with `was` = theirs). Formulas whose value changed because a cell they read
  changed are sent too (with `was` = what the browser knew); they never conflict on a computed value. Tab settings
  (widths, frozen panes, totals, colour rules, charts) replace the tab's — *(security review 2026-10)* only when the etag is current: a
  stale save's tab settings aren't applied (they would overwrite someone else's); the answer is 409 with
  `tabsNotSaved: [tab names]` (the cells in the request are still saved, as above), the editor shows the newer sheet
  and asks to do them again. Changes of **shape** —
  rows / columns inserted or deleted, a sort, tabs added, renamed, moved or deleted — send `{etag, sheet}` (the whole
  sheet) and need an unchanged file: otherwise 409 `{stale: true, sheet, etag}`, the editor shows the newer sheet and
  asks to do it again. An answer to a save whose etag was stale carries `sheet` (with others' changes) and `by`, so
  the editor shows them; changes made in the browser meanwhile are put back on top. The 10-second check reloads the
  sheet when nothing is waiting to be saved.
- **Checklists** save operations (`add`, `edit`, `tick`, `untick`, `move`, `indent`, `delete`, `untickAll`) applied to
  the current file under the lock, items found by text + occurrence; two people ticking different items never
  conflict. An item that's no longer there answers 409 for that item only.
- **Autosave** 1 s after typing stops and on leaving. An open document checks its etag every 10 s while visible;
  "Alex is editing" shows when someone saved in the last 30 s.

### 7.3 Versions (`.versions/<node-id>/`)
- Before the app overwrites a document, the current file is copied to `.versions/<node-id>/<n>.<ext>` — at most one
  per editor every 5 minutes, plus every conflict, restore, and **every first save after a change made outside the
  app** (so outside edits are in history too). Up to `versions_kept`; older ones deleted.
  *(built)* Before an app write: no version for an empty file or for content already in History (same SHA-256);
  "outside the app" when the file on disk isn't what the app last wrote; a version when the last author is
  someone else; for the same author only if no version was kept by their saves in the last 5 minutes. A version's
  `made_by` is the author of the kept content (NULL = outside), `saved_by` whose save kept it.
- *History* lists versions (who — or "outside the app" — when, size); *Restore* writes the old file back as a new
  version. Versions follow the node through renames and moves (they're keyed by id).
- Only documents (notes, checklists, sheets) get versions; uploaded files don't (replacing one keeps the previous
  copy for 7 days, §9.2).
  *(built)* That copy is a `versions` row like any other (the file's own *Earlier copies* lists it, with Download
  and Restore); housekeeping removes such rows of non-document files after 7 days (`versions.prune_replaced`).
  Replacing a document by upload keeps the old text as an ordinary version (History).

### 7.4 Trash (`.trash/`)
- Deleting moves the file or folder to `.trash/<owner folder>/<trash id>/` with an `.item.json` (original path,
  node id, who, when). Admin shared folders keep their own hidden `.trash` inside the shared folder.
- Restore puts it back (or into My docs / the shared folder's top if the parent is gone, " (restored)" added on a
  name clash). Emptied after `trash_days`.
- *(security review 2026-10)* **Restore** goes into the folder the item was in — that same folder by id (the node keeps `parent_id` while
  in Trash), wherever it is now — never into whatever folder now sits at the old path (another folder, perhaps
  shared with Everyone); when that folder is gone or in Trash, into the top of the owner's folder (or the admin
  shared folder).
- *(security review 2026-10)* **Trash is private**: a node in Trash is reachable only by the owner of that Trash (person roots) or people
  with Read and write on that admin shared folder — in the access CTE (shares, Everyone, parents and read-only
  folder access don't reach trashed nodes) and in `role_of`. So search (with or without *Trash*), Activity, follows
  (the deletion of a followed item isn't told to people it was shared with), links / backlinks, pins, favourites,
  sensors and the storage report leave it out for everyone else.

### 7.5 Limits
`max_doc_mb` per document in the editor; `upload_mb`; `quota_gb` per person; folder depth 10; sheets 10 tabs,
5 000 rows × 100 columns per tab, 200 000 filled cells; checklists 2 000 items. *(built)* Sheets also: a cell holds at
most 32 767 characters, a formula 8 192; 50 colour rules and 10 charts per tab; a frozen area of at most 50 rows and
20 columns; column widths 24–1 000 px. Checked in the browser and again by the server (`sheet_model.clean_sheet`).

## 8. Sheets

### 8.1 Grid
- Column letters, row numbers, freeze rows/columns, column widths, insert/delete rows and columns (references adjust),
  sort a range, filter a column, fill down/right (Ctrl+D / Ctrl+R; `$` fixes a reference), copy/paste with Excel and
  Google Sheets (tab-separated text).
- **Formats:** General, Number (decimals), Currency (HA's currency by default), Percent, Date, Text; bold; alignment;
  optional red for negatives.
- **Totals row** under the data (SUM / AVERAGE / COUNT / MIN / MAX per column). Selecting a range shows Sum, Average,
  Count, Min, Max in the status bar.
- On phones: a cell editor bar at the top, long-press for row/column actions.

### 8.2 Formulas (`sheetcalc.js`)
- `=` starts a formula, parsed into a tree (no `eval`; the CSP forbids it).
- **Operators:** `+ - * / ^`, unary `-`, postfix `%`, `&`, comparisons `= <> < > <= >=`, parentheses.
- **References:** `A1`, `$A$1`, ranges `A1:B10`, whole columns `B:B`, other tabs `'Car'!B4`.
- **Functions:** `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `COUNTA`, `ROUND`, `ROUNDUP`, `ROUNDDOWN`, `ABS`, `IF`,
  `AND`, `OR`, `NOT`, `SUMIF`, `COUNTIF`, `AVERAGEIF`, `TODAY`, `DAYS`, `LEN`, `UPPER`, `LOWER`, `CONCAT`, plus the
  §17.10 set.
- **Errors:** `#DIV/0!`, `#REF!`, `#NAME?`, `#VALUE!`, `#CYCLE!`, `#N/A`, each explained on hover/tap.
- **Recalculation** through a dependency graph; 50 000 filled cells recalculate in under 100 ms on a mid-range phone.
- Runs in the browser. Saves send what was typed and the computed values; the server writes both into the file
  (formula + cached value in `.xlsx`, so Excel and other apps show the numbers straight away).
- An **ƒ** button lists functions with examples; typing `=SU` suggests `SUM`, `SUMIF`, `SUMIFS`.

### 8.3 `.csv` sheets
- One tab; cells are values or `=` formulas (Excel and LibreOffice read those as formulas). Formats, widths, colours
  and charts aren't saved — the editor says so once and offers **Convert to .xlsx** (keeps the `.csv` as a version).
- Read: comma, semicolon or tab (detected); UTF-8 / UTF-16 with BOM. Written as UTF-8 with the separator it had.

### 8.4 `.xlsx` sheets (`formats/sheet_xlsx.py`, openpyxl)
- **Kept:** values, formulas (text) with cached values, number formats, bold, alignment, column widths, frozen panes,
  tabs and their order, conditional formatting the app supports (§17.10), charts the app made (bar/line/pie, written as
  Excel charts).
- **Files the app made** carry a custom document property `HouseholdDocs=1` and open for editing.
- **Files made elsewhere** are checked on open: if they contain anything the app can't keep (macros, pivot tables,
  images, comments, data validation, merged cells, external links, charts not made by the app, unknown functions),
  they open **read-only** with a list of what's in them and **Save a copy to edit** (a new `.xlsx` the app owns).
  Otherwise they open for editing.
- Formulas the app doesn't know are shown with their cached value and `#NAME?` only if recalculated; the cell is
  marked "calculated by Excel".
- **Export:** `.xlsx` (the file itself), CSV of a tab (values or formulas), print. **Import:** `.csv` and `.xlsx` into
  a new sheet.

### 8.5 How it was built (steps 5 and 10)
- **The sheet as JSON** (`formats/sheet_model.py`, the same shape in the browser): `{tabs: [{name, kind: "grid", cells:
  {"B4": {v, q?, c?, f?, d?, red?, b?, al?, x?}}, cols: {"A": px}, freeze: {r, c}, totals: {"B": "SUM"} | null, cond:
  [rule], charts: [{id, type, range, title}]} | {name, kind: "chart", chart: {type, title, source: {tab, range}}}]}`.
  `v` is a number, text, TRUE/FALSE or a formula (`"=…"`); `q` keeps text that starts with `=` as text (Excel's
  quote prefix); `c` is the value the browser computed for a formula; `f` the format (`number`, `currency`,
  `percent`, `date`, `text`; none = General) with `d` decimals and `red` for red negatives; `b` bold; `al`
  left/center/right; `x` marks a formula the app can't calculate (its value is Excel's, shown with a corner mark and
  "Calculated by Excel"). A new sheet's first tab is "Sheet1" (as in Excel). Tab names follow Excel's rules (1–31
  characters, none of `: \ / ? * [ ]`, not "History", unique without case).
- **The engine** (`static/sheetcalc.js`): a tokeniser and a precedence parser into a tree, compiled into closures.
  Excel's semantics where they're visible: `-2^2` = 4, `2^3^2` = 64, text compares without case and numbers < text <
  TRUE/FALSE, an empty cell is 0 or "", text that looks like a number is a number in arithmetic but skipped in a
  range by SUM / AVERAGE / COUNT, criteria with `<>=` and wildcards `* ? ~`, binary search for approximate lookups,
  half-away-from-zero rounding, dates as Excel serials (with Excel's 29 Feb 1900). Errors `#DIV/0! #REF! #NAME?
  #VALUE! #CYCLE! #N/A` and also `#NUM!` (a number out of range: `PMT` with no periods, a date before 1900), each
  explained in the status bar and on hover. Beyond the spec's list: `CONCATENATE` (Excel's older name, common in
  files from elsewhere). Whole columns (`B:B`) and rows (`1:1`) stop at the tab's last used row. `TODAY()` is today
  in Home Assistant's time zone and is recalculated when the day changes. Recalculation keeps an index of who reads
  each cell (single cells, and ranges by column); a change recalculates only what depends on it, in dependency order
  (Tarjan's strongly connected components on the cells: a loop is `#CYCLE!`, and whatever reads it shows the error).
  **Benchmark** (`tests/js/sheetcalc.test.js`, Node 22 on the build machine, an x86 container): 50 000 filled cells
  (10 000 rows: text, two numbers, `=B*C` and a 10 000-long running total, plus SUM / SUMIF of whole columns) —
  first full recalculation ~100 ms (it builds the dependency index and warms the JIT), then 12–20 ms; an edit at
  the top of the running total (10 000 formulas change) ~20–25 ms, an edit near the end ~7 ms. A mid-range phone is
  roughly 3–5× slower: under 100 ms after the first.
- **References adjust** when rows or columns are inserted or deleted (on every tab: `'Car'!B4` too; `$` references
  move as in Excel; a deleted cell becomes `#REF!`, a range shrinks or grows), on fill down / right and paste
  (relative parts move, `$` parts stay, off the edge is `#REF!`), and when a tab is renamed (every formula naming
  it, and chart tabs showing it). **Sorting** moves whole rows of the range; formulas in moved rows keep pointing at
  their own row, while references to other tabs stay where they were (*decision*: Excel would move those too,
  which silently breaks cross-tab lookups). Widths, totals, colour rules and charts follow inserted/deleted columns.
- **The grid** (`static/sheet.js`) draws only the rows on screen (a fixed row height, spacer rows above and below),
  every column up to the used ones + a few (≤ 100); frozen rows and columns and the header row / row numbers are
  `position: sticky` cells. Selecting doesn't rebuild the grid (the cell under the pointer must stay in place).
  Typing starts editing in the cell (desktop) or through the cell editor bar at the top (phones: tap a cell, tap the
  bar); clicking cells while typing a formula puts their address in, and so do the **arrow keys** right after `=`, an
  operator, `(` or `,` (`sheet.js` `pointWithArrow`: the first arrow inserts the neighbouring cell's address, later
  ones move it, Shift extends it to a range; typing anything else keeps the reference and ends pointing). Keys: arrows (Shift extends), Tab / Enter,
  F2, Delete, Ctrl+C / X / V (tab-separated text to and from Excel and Google Sheets — a copy within the sheet keeps
  formulas, moved), Ctrl+D / Ctrl+R (fill down / right), Ctrl+B, Ctrl+Z / Ctrl+Y (undo / redo within the visit),
  Ctrl+A. Right-click — or a long press on phones — on a column letter, row number or cell opens their menu: insert
  / delete, sort A→Z / Z→A, filter, width, freeze, total, fill, clear. Column widths can also be dragged. The
  **filter** shows the rows whose value in that column is ticked; it is for that page only and isn't saved
  (*decision*: an Excel AutoFilter in a file from elsewhere makes it read only, see below). **Typing** `12%` gives
  a percent, `2026-03-09` or `9/3/2026` (day first) a date, `TRUE`, numbers with `,` thousands, `'text` text as it
  is. The **ƒ** button lists every function with its arguments, what it does and an example; typing `=SU` suggests
  SUM, SUMIF, SUMIFS. The **totals row** is a sticky row under the grid (SUM / AVERAGE / COUNT / MIN / MAX per
  column, tap a total to change it; Σ adds a SUM under every column with numbers). The **status bar** shows Sum,
  Average, Count, Min and Max of a selection (in the first column's format), or what an error means.
- **Formats** come from the browser (`Intl.NumberFormat`): Currency is Home Assistant's currency (`GET /config` →
  `currency`, read with the time zone at start-up; EUR until known); in the `.xlsx` it is `"€"#,##0.00`
  (`#,##0.00 "CHF"` for codes without a one-character symbol), Number `#,##0.00`, Percent `0.0%`, Date `yyyy-mm-dd`,
  Text `@`; red negatives add `;[Red]-…`. Formats read from other files map back to these; one the app has no
  equivalent for (times, scientific, fractions) makes the file read only.
- **Reading an `.xlsx`** always happens in `formats/xlsx_worker.py`, a separate `python -I` process that limits itself
  first (1 GB of address space, CPU time, no file writes, 64 open files) and is killed after 20 s; at most two run
  at once. It checks the zip (≤ 4 000 parts, ≤ 64 MB unpacked by the central directory — the zip reader also stops a
  part at its declared size — no encrypted parts), refuses any XML part with a DTD or entity declaration (defusedxml
  would too; the early check says why), streams the cells once (openpyxl read-only mode) to count them against the
  caps (10 tabs, 5 000 rows, 100 columns, 200 000 filled cells) and collect the cached values, and only then loads
  the workbook. Macro-enabled workbooks and templates are relabelled in memory so their cells can be shown. Results
  are cached by content (SHA-256) in the app, so saving and searching don't parse the same file twice.
- **What makes a file read only** (listed in the editor, *Save a copy to edit* writes a new `.xlsx` beside it — or in
  My docs when you can't add there — with what the app keeps): macros, pivot tables, pictures, comments or notes,
  data validation, merged cells, links to other workbooks, charts the app didn't make, functions the app doesn't have
  (named in the list), and also (*decision*, all things a save would lose): Excel tables, slicers, form controls and
  embedded objects, named ranges, array formulas, hidden tabs, hidden rows or columns, an AutoFilter, protected tabs,
  colours / borders / fonts other than bold, number formats and conditional formatting the app doesn't have, and any
  other part it doesn't know. The app's own files are recognised by `HouseholdDocs=1`; `HouseholdDocsMeta` (JSON) lists
  per tab the charts and the totals row it wrote — a chart in the file that isn't in that list (added in Excel) makes
  even the app's own file read only.
- **Writing an `.xlsx`** (in the app, from a checked sheet): openpyxl, then the computed values are put into the sheet
  XML (`<f>…</f><v>…</v>`, `t="str"/"b"/"e"`; openpyxl writes formulas without them), with `fullCalcOnLoad` so Excel
  recalculates. Newer functions get Excel's `_xlfn.` prefix (`CONCAT`, `DAYS`, `XLOOKUP`) and lose it on reading.
  Text starting with `=` gets the quote prefix. The totals row is written as real cells under the last filled row
  (`=SUM(B1:B<last>)`, bold, a "Total" label in the first free column) and recorded in the meta, so Excel shows it
  and the app takes it out of the grid again on reopening. Conditional colours become Excel rules: greater / less than
  and between `cellIs`, text contains `containsText`, date before / after an expression
  `AND(ISNUMBER(B2),B2<DATE(y,m,d))`, top / bottom N `top10`, duplicates `duplicateValues`; colours are six named
  ones (red, amber, green, blue, purple, grey — soft fills and text colours that read in every theme; Excel gets fixed
  hex values, read back to the nearest). Charts: bar (columns), line and pie from a range whose first row names the
  series and first column holds the labels; below the grid they're anchored under the data, on their own tab they're
  an Excel chart sheet. In the app they're SVG drawn in the theme's colours.
- **`.csv` sheets** (`formats/sheet_csv.py`): the separator is the one (comma, semicolon, tab) that splits the first
  lines most evenly; UTF-8 with or without a BOM, UTF-16 with a BOM, else Windows-1252. A value becomes a number only
  when writing it back gives the same text (`007`, `1.50`, `0.10` stay text), `yyyy-mm-dd` a date; so re-saving a
  file changes only the cells that were changed. Written in UTF-8 (a BOM if it had one or was UTF-16), with its
  separator and line endings; dates as `yyyy-mm-dd`. The editor hides what a `.csv` can't keep, says so once, and
  offers **Convert to .xlsx** (`POST /api/docs/{id}/convert`): the same item (shares, favourites, history) becomes
  `<name>.xlsx`; the `.csv` is kept under History (download only — restoring it into the .xlsx is refused).
- **Export** (`GET /api/docs/{id}/export?format=xlsx|csv&tab=&mode=values|formulas`): `.xlsx` is the file itself (a
  `.csv` sheet is converted for the download); CSV of a tab through `common/csv_export.py` — UTF-8 with a BOM, text
  guarded against formulas, numbers as numbers, dates as `yyyy-mm-dd`, formulas as typed only in `formulas` mode.
  **Import** (`POST /api/import?name=&parentId=&format=`, the body is the file, ≤ `max_doc_mb`): a `.csv` becomes a
  new sheet in the person's `newSheets` format, an `.xlsx` a new `.xlsx` with what the app keeps (the answer lists
  what was left out). **Print** builds a print-only page: title (with the tab), owner, date, the used cells with
  gridlines, the header row (and frozen rows) repeated on every page, the totals row, the tab's charts; a hint to
  choose Landscape when there are more than 7 columns.
- **Search**: a sheet's text is what every filled cell shows (all tabs; formulas their value, dates as yyyy-mm-dd), up
  to 20 000 cells, with `nodes.sheet_cells`; an `.xlsx` changed outside the app is read by the worker during the scan.
  A result whose text matched has `cell: {tab, ref}` and the snippet `Budget › Car › C14: …`; it opens at
  `#/doc/<id>/cell/<tab>/<ref>` with the cell selected and flashing.

## 9. Admin shared folders

### 9.1 Admin → Shared folders
- **Add shared folder:** a folder browser over `/share`, a name ("House papers"), then per person **No access / Read
  only / Read and write**, plus an **Everyone** row (new people get it too).
- Edit, rename, change access, **Rescan now**, **Stop sharing** (nothing on disk changes).
- A missing folder (network drive not mounted) shows ⚠ to admins and is hidden from others.
- **Can't be shared:** `/share` itself, hidden folders, the docs folder or anything inside it or containing it (that
  would expose everyone's documents), Household Chat's files folder.

### 9.2 What people see
- The same lists, editors, search and file handling as in My docs (§5, §7, §8), with the folder's mode:
- **Read only:** open, search, preview, download (also a folder as .zip), copy into My docs.
- **Read and write** adds: new note / checklist / sheet, new folder, rename, move inside the folder, upload,
  **delete → the folder's hidden `.trash`**, versions in the folder's hidden `.versions` (same rules as §7.3).
  Replacing an uploaded file keeps the previous copy in `.versions` for 7 days.
- Admin shared folders have no owner and no per-item sharing; access is the folder's.

### 9.3 Path rules (`share_paths.py`, shared with Household Chat)
- Every request names a **node id** or a **root id + relative path**; the server joins, normalises, `realpath`s and
  checks the result is inside the root's `realpath` — on every request (a folder later swapped for a symlink is
  refused).
- Hidden entries (leading `.`) and symlinks pointing out of the root are skipped and refused.
- File names: no `/`, `\`, NUL, leading `.`, or names Windows can't hold.
- *(built)* These rules are `store/paths.py` (`resolve`, `check_name`, `clean_name`) plus `roots.root_real`'s
  per-request checks of the root itself (§9.4); they move to `common/python/share_paths.py` when Household Chat
  adopts them.

### 9.4 How it was built (step 3)
- **Storing and checking a folder** (`share_folders.py`, `store/roots.root_real`): the folder is stored as its *real*
  path relative to `/share` (a link chosen in the browser is resolved once, when it's added). On every use the real
  path must still equal that path (a folder later swapped for a link — even one pointing to the same place — is
  refused with "replaced by a link — an admin needs to look at it"), must not overlap the documents folder, and
  must exist (else 404 "That shared folder isn't available right now", the index marks the root `missing` and
  marks nothing gone). *(security review 2026-10)* Also on every use: it must not be (inside) Household Chat's files folder or another
  Docs install's documents folder (a marker file on the way up — e.g. Chat's folder later set to a folder above
  it); a store that turns up *inside* it is skipped (§5.5) and Admin → Shared folders shows it as a problem
  ("Household Chat's files folder is inside this folder …", the folder still usable).
- **Can't be shared** (checked on adding, and shown as you browse or type: `POST /api/admin/shared-folders/inspect`):
  `/share`, outside `/share`, hidden folders, the documents folder or anything inside or around it, a folder holding
  (or inside) another install's `.household_docs` marker, Household Chat's files folder (inside or around it),
  **another admin shared folder inside or around it** (decision: each file belongs to exactly one root), a folder
  the app can't read. A folder the app can't write can be shared; *Read and write* then fails with the file
  system's message.
- **Names are unique** (case-insensitive) — `in:"House papers"` finds a folder by it. Editing changes the name and
  access; the folder itself isn't editable (stop sharing and add it again). *Stop sharing* forgets the index rows,
  Trash entries and access; the folder, its hidden `.trash` / `.versions` / `.tmp` stay on disk.
- **Access**: `root_access` rows per person (`ro` / `rw`) plus `user_id = '*'` for Everyone; a person gets the
  better of their own row and Everyone's. Turned-off people see nothing. Admins get access only through these rows.
  A missing folder is left out of every list and search (the access CTE skips missing shared roots) and is shown
  with ⚠ to admins (Admin → Shared folders, and their own `GET /api/shared-folders`).
- **No per-item shares** in shared folders: `set_share` refuses (403/409), the access CTE ignores any share on a
  node in a shared root, and a file moved in from a person's folder outside the app (matched by inode) loses its
  shares.
- **Places**: the top of a shared folder has no node; the API names it `root:<id>` (`GET /api/list?node=root:…`,
  `POST /api/docs {parentId: "root:…"}`, `POST /api/nodes/{id}/move {parentId: "root:…"}`, uploads, zips). People
  with *Read and write* move anything anywhere inside the folder (no "shared tree" rule there); moving between two
  spaces is refused (use Make a copy).
- **Trash**: `<folder>/.trash/_shared/<trash id>/`; Trash lists each shared folder you can write with its deleted
  items, and anyone with *Read and write* can restore them; emptied after `trash_days` like every Trash. Versions of
  documents go to `<folder>/.versions/`.
- While the documents folder isn't usable, writing into shared folders is refused too (one write guard for the app;
  reading still works).
- **Uploads** (`POST /api/nodes/{place}/upload?name=&onClash=keep|replace`, `place` = folder id, `root:<id>` or
  `mine`): one file per request, **the body is the file** (no multipart: nothing is spooled to the container's
  `/tmp`), streamed into the root's `.tmp/` while counting (`upload_mb`: a bigger `Content-Length` is refused before
  reading, a body that grows past it is cut off and deleted), then linked into place without overwriting a name
  someone took meanwhile. The name is cleaned (`/ \ : * ? " < > |` and control characters → `_`, leading dots and
  trailing dots/spaces dropped, reserved names get `_`). `keep` (default) adds " (2)"; `replace` replaces a file of
  the same name in any case (as on Windows) — the item keeps its id, shares and favourites, and the previous copy is
  kept (§7.3); a folder of that name is never replaced. Quota is checked before and after reading. The page asks
  once when names are already in the folder (Replace / Keep both / Skip). Leftover `.tmp` files in shared folders are
  cleaned up hourly too.
- **.zip downloads** (`GET /api/nodes/{folder | root:<id> | mine}/zip`, `GET /api/zip?ids=a,b,…` for up to 200
  items): built from a walk of the disk (what's really there, including files the index hasn't seen yet), hidden
  entries and links left out, every file realpath-checked inside its root and opened with `O_NOFOLLOW`; names inside
  are made from the path parts (`/`, `\`, control characters → `_`, never `.` or `..`), two top items with the same
  name get " (2)". At most **10 000 files and 4 GB**, depth 12 — over that it's a 413 before anything is sent.
  Streamed while it's written (deflate, level 1).

## 10. Search

One search over everything **you can open**: My docs, Shared with me, Everyone and **your shared folders**. Search is in
the header on every page (**/** or **Ctrl+K**), with a full **🔎 Search page** for the advanced options.

### 10.1 Where to look (scope)
- **Everywhere** (default), **This folder** (with or without subfolders), **My docs**, **Shared with me**,
  **Everyone**, **Shared folders** (all), or **one shared folder**. Several can be ticked. **Include Trash** (off).

### 10.2 What to match
- **Name** — the file or folder name:
  - **Contains** (default; every word, any order), **Starts with**, **Exact**, **Ends with**.
  - **Wildcard pattern:** `*`, `?`, `[abc]`, `[0-9]` — e.g. `*.csv`, `bill-2026-??.pdf`, `IMG_[0-9]*`.
    Case-insensitive.
  - **Regular expression** (when `regex_search` is on) — e.g. `^invoice-\d{4}-\d{2}` (§10.4).
  - **Exclude words:** `-draft`.
- **Content** — text inside notes, checklist items, sheet cells (all tabs) and other indexed text files
  (`.txt`, `.md`, `.csv`, `.json`, `.yaml`, `.log`, `.xml` as plain text; PDFs from step 9):
  - Words, `"exact phrase"`, `word*`, `-exclude`, `OR`.
  - **Pattern in content** (regex), e.g. `\b\d{4}-\d{4}\b`.
  - Snippets: the line, the checklist item, or `Budget › Car › C14`. *(built, step 5: §8.5)*
- **Name or content** (default), **Name only**, **Content only**.
- **Path** — `path:bills/2026`, with wildcards.

### 10.3 Filters
| Filter | Choices |
|---|---|
| **Modified** | Today · Yesterday · Last 7 days · Last 30 days · This month · Last month · This year · Last year · **Custom range** · **Before** / **After** a date |
| **Created** | The same choices (the file system's time where it has one, else first seen by the index) |
| **Type** | Note · Markdown · Checklist · Sheet · Folder · Image · PDF · Spreadsheet · Document · Archive · Video · Audio · Other · **Extension** |
| **Size** | Any · < 100 KB · 100 KB–1 MB · 1–10 MB · 10–100 MB · > 100 MB · custom |
| **Modified by** | A person · Me · Not me · Changed outside the app |
| **Owner** | A person · Me |
| **Shared** | Shared by me · Shared with me · Not shared · Shared with Everyone |
| **Access** | I can edit · Read only |
| **Other** | ⭐ Favourites · Checklists with open items · Checklists all done · Empty folders · Duplicates (same SHA-256) · Tag · Colour (§17.1) |

### 10.4 Pattern and regex rules
- Wildcards become an anchored, case-insensitive pattern; plain word searches never use regex.
- **Regex** (Python `re`, help popover with examples): up to 200 characters, compiled first (errors shown under the
  box). Name regex runs over indexed names; content regex narrows candidates through FTS on any literal words, then
  reads each candidate (≤ `content_index_mb`).
- Runs in a **separate worker process** with a 5-second limit; past it the worker is killed and partial results come
  back with "Search stopped after 5 s — narrow it down". One regex search per person at a time.
- Filters and access checks run in SQL; nothing is filtered in the browser after the fact.

### 10.5 Query syntax
`name:*.pdf` · `name:/^bill-\d+/` · `ext:xlsx` · `type:sheet` · `modified:7d` · `modified:>2026-09-01` ·
`modified:2026-01-01..2026-03-31` · `created:<2025` · `size:>5mb` · `by:alex` · `by:me` · `by:outside` ·
`owner:priya` · `in:"House papers"` · `in:mine` · `path:taxes/2026` · `tag:taxes` · `color:red` · `is:fav` ·
`is:shared` · `is:open` · `is:dup` · `content:/\b\d{4}-\d{4}\b/` · `"exact phrase"` · `-word` · `OR`.
Chips show every filter in use; tapping one removes it.

### 10.6 Results
- Columns: name (icon, match highlighted), location (`House papers › Taxes › 2026`), modified, modified by, size, type.
  Sort by relevance, name, modified, created, size, type, location; ascending/descending.
- **Group by** none / location / type / modified (Today, This week, Earlier).
- Up to 500 results, 50 per page, with a count and time.
- **Open** (a sheet result selects the cell, a note scrolls to the line), **Show in folder**.
- **Bulk actions** (where allowed): Download (.zip), Copy to My docs, Move, Favourite, Tag, Delete. **Export the result
  list** as CSV (names, locations, dates, sizes — no contents).
- **Save this search** (optionally pinned to the sidebar); **recent searches** (last 20).
- Search as you type (250 ms) for words; regex and large scans on Enter.

### 10.7 Speed targets
Name, filter and word searches over 50 000 indexed entries: under 300 ms on a Raspberry Pi 4. FTS content search:
under 500 ms. Regex: bounded by §10.4.
*(built)* `tests/test_search_speed.py` fills the index with 50 000 synthetic rows over two people and an admin shared
folder and times searches through the API (best of 3). On the build machine (an x86 container): name contains /
starts / wildcard 65–80 ms, filters only ~70 ms, path filter ~120 ms, name or text ~115 ms, FTS content ~170–200
ms, duplicates ~65–100 ms, name regex in the worker ~360 ms, text regex ~120 ms. A Pi 4 is roughly 3–4× slower,
which keeps names and filters near the 300 ms target and FTS under 500 ms.

### 10.8 How it was built (step 4)
- **The request**: `GET /api/search?q=…` plus the page's options — `match` (any / name / content), `nameMode`
  (contains / starts / exact / ends / wildcard / regex), `contentMode` (words / regex), `scope` (comma list: all,
  mine, shared, everyone, folders, trash, `root:<id>`, `folder:<id>` with `sub=0|1`), `trash`, and the filters
  `modified created type ext size by owner shared access is path tag color` (the same values as the box's syntax),
  `sort` + `dir`, `group`, `page`, `limit` (≤ 500). The box's filters and the page's are ANDed; every one comes back
  as a chip — `token` (the text to take out of the box) or `param` (the option to clear) — and a bad value is an
  error chip that matches nothing. A saved search keeps `q` and the options (`filters` JSON).
- **Words**: each plain word matches the start of a word in the text (as in step 1, so `word*` is the same);
  `"phrase"` is exact; `-word` / `-"phrase"` leave out what has it (in the name or the text, by `match`); `OR` binds
  the two words beside it (`car tent OR stove` = car and (tent or stove)) — built as an explicit FTS5 expression.
  In names, "contains" means every word (or phrase) somewhere in the folded name. **Name or text** = all words in
  the name, *or* all words in the text; relevance puts name matches first, then FTS5's bm25.
- **Name modes**: starts / exact / ends compare the folded name *and* its title (without the extension); the whole
  box (minus `key:value` filters) is one pattern for starts, exact, ends, wildcard and regex — quotes, dashes and OR
  are part of it. In "contains" mode a bare word with `*`, `?` or `[` is a name wildcard (`*.pdf`, `IMG_[0-9]*`);
  `word*` (one trailing star) stays a word. Wildcards run as SQLite `GLOB` over the folded name (`[!x]` → `[^x]`),
  anchored; `pattern.wildcard_to_regex` gives the anchored, case-insensitive regex used to highlight. Starts / exact /
  ends / wildcard look at names only.
- **Regular expressions** (`regex_worker.py`): checked first (≤ 200 characters, compiles). The candidates come from
  the same SQL (scopes, filters, words, access) — up to 200 000 names / 5 000 files — and go to
  `python -I -S regex_worker.py` (standard library only) as JSON lines; matches come back as they're found.
  The worker is killed at 5 s (`stopped: true`, "Search stopped after 5 s — narrow it down") or as soon as enough
  matches are in. Case-insensitive, `^`/`$` per line in text. Text patterns read each candidate file (≤
  `content_index_mb`, opened with `O_NOFOLLOW`, after the realpath check) and return the first matching line with
  its number. *Difference:* candidates are narrowed by the literal text every match must contain (the top-level
  literal runs of the pattern, ≥ 3 ASCII characters) with `LIKE` on the indexed text rather than FTS words — a
  literal can be part of a word (`voice-` in `invoice-2026`), which FTS tokens would miss. One regex search per
  person at a time (429 otherwise), at most 3 workers for the whole app. Off: `regex_search` = false → 403.
- **Filters** (`search/filters.py`): dates in Home Assistant's time zone, converted to UTC bounds — `today`,
  `yesterday`, `Nd` (today and the N−1 days before), `Nw`, `Nm` (30·N days), `Ny`, `thisweek` / `lastweek` (weeks
  start on Monday), `thismonth`, `lastmonth`, `thisyear`, `lastyear`, a day / month / year (`2026-03-14`, `2026-03`,
  `2026`), `>D` (after that day), `>=D` (from it), `<D` (before it), `<=D` (up to the end of it), `A..B` (both days
  included; either side may be left out). **Created** is the time the index first saw the file (Linux keeps no
  creation time). Sizes: `>5mb`, `<100kb`, `1mb..10mb` (both included), `5mb` (5 MB up to 6 MB), units b / kb / mb /
  gb (1024-based) with decimals; folders never match. Types: note, markdown, checklist, sheet, folder by kind; image,
  pdf, spreadsheet, document, archive, video, audio by extension; other = files with none of those extensions.
  `by:` a person (id, user name, display name, or the start of it), `me`, `notme` (anyone else, outside included),
  `outside`; `owner:` a person or `me` (things in their folder). `shared:` `byme` (your things with a share on them
  or a folder above), `withme` (others' things shared with you, not via Everyone), `everyone`, `not` (your things
  with no share). `access:edit` / `read`. `is:fav`, `is:shared` (any share above, or in an admin shared folder),
  `is:open` / `is:done` (checklists, from `nodes.check_open/check_done`), `is:empty` (folders with nothing live
  inside), `is:dup` (same SHA-256 as another file *you can open*). `path:` matches the folders an item is in,
  written from its space's name (`/House papers/Taxes/2026/`, `/<person's folder>/Trip/`), a run of folder names
  anywhere in it, `*` / `?` inside a part. `color:` uses `nodes.color` and `tag:` the
  item's tags (both built in step 7, §17.1). `-ext:`, `-type:`, `-is:`, `-tag:` exclude.
- **Results**: up to 500 (`more` says there were more), 50 per page; each with location parts ("My docs" for your
  own, the person's folder name for others', the shared folder's name, "Trash"), the name with ⁅ ⁆ around the match,
  a snippet (FTS5 `snippet()` or the worker's line), `line` (the first line holding one of the words, for notes and
  checklists — the editor opens there), `typeLabel`, `group`. Grouping (location / type / modified: Today, This
  week, Earlier) sorts the result list by group, keeping the chosen order inside each. The CSV (`GET
  /api/search/export`, the same parameters, ≤ 500 rows) has Name, Location, Type, Modified (UTC), Created (UTC),
  Modified by, Size (bytes) — UTF-8 with a BOM, text cells guarded against formulas (`common/csv_export.py`).
- **Saved and recent**: at most 50 saved searches per person, pinned ones in the sidebar under 🔎 Saved searches (and
  in `/api/me` → `pinnedSearches`); `#/searches` pins, renames and deletes them. A search is added to the recent 20
  on Enter and when a result is opened (not on every keystroke); words search as you type (250 ms), patterns only on
  Enter. The header box opens the Search page; Ctrl+K or / focuses it.
- **Access**: the access CTE is part of the result query, the regex candidate query and the duplicate check;
  `tests/test_search_page.py` runs ~50 kinds of search (every mode and filter, Trash, scopes, export) as four people
  and checks every item in every answer and CSV against `role_of`.

## 11. Notifications
- "Alex shared 'Trip 2026' with you" through `ha_notify.py` and the person's phones (`ha_people.py`), plus admin extras
  in Admin → People → 🔔, *Send a test*. Per-person toggle. Never contains document text. Follows: §17.4.
  *(built)* Only a **new** share with a person notifies (not a role change, not Everyone shares — those would
  notify the whole household); a transfer tells the new owner. The per-person toggle is `notifyShares` in Settings
  (on by default).
  *(built)* A notification's `url` / `clickAction` is `config.INGRESS_URL`, set by `app_messages.learn_panel` at
  start-up from the Supervisor's `addons/self/info`: the sidebar page `/<full slug>` (`/a0d7b954_household_docs`),
  or `/app/<full slug>` when the app has no sidebar page. The older `/hassio/ingress/<slug>` is a 404 in current
  Home Assistant, so it is never used.

## 12. API (ingress; 404 when you can't see it)

| Method & path | Who | Purpose |
|---|---|---|
| GET `/api/me` · PUT `/api/me/settings` · GET `/api/whoami` | anyone | As the siblings; `noAdmin`, `docsFolder` status; `me` makes the person's folder on the first visit; settings `{notifyShares, foldersFirst, sort}`; *(step 12)* `apps {chat, todo, panel}`, `isChild`, `kidsView [{rootId, childId, name}]` |
| GET `/api/people` | access | People with access (Share and Transfer pickers) |
| GET `/api/space/{mine\|shared\|everyone\|favourites\|recent\|trash}[?hidden=1]` · GET `/api/list?node=&sort=` · GET `/api/folders?node=` · GET `/api/nodes/{id}` | access | The spaces (Trash adds `folders`: the admin shared folders you can write, with their deleted items); a folder's children (after a one-folder scan) with crumbs, `ref`, `mode` (ro/rw in a shared folder); the Move picker's folders; one item with its crumbs and `/share` path (no content anywhere). `node` may be `root:<id>` (a shared folder's top) |
| POST `/api/docs` | editor of parent | Create `{kind, name, parentId, format?}` → writes the file (`format`: a sheet's `xlsx` or `csv`) |
| GET `/api/docs/{id}` · `…/etag` | viewer | Content + etag · etag only (polling) |
| PATCH `/api/docs/{id}` | editor | Note `{etag, text}` · sheet `{etag, cells: [{tab, ref, was, now}], tabs?}` or `{etag, sheet}` (§7.2); 409 when stale (sheets: per cell) |
| POST `/api/docs/{id}/checklist` | editor (viewer: tick only, if allowed) | `{ops:[…]}` |
| POST `/api/nodes/{id}/rename\|move\|copy\|transfer\|leave\|hide\|favourite` | per §6 | A change that takes the item out of the caller's reach answers `{id, outOfReach: true}` |
| DELETE `/api/nodes/{id}` · POST `/api/trash/{id}/restore` · POST `/api/trash/empty` | editor · owner · owner | Trash |
| GET `/api/docs/{id}/versions` · GET `…/versions/{n}` · POST `…/versions/{n}/restore` | viewer · viewer · editor | History (a version's text, to view or compare) |
| GET / POST / DELETE `/api/nodes/{id}/shares[/{userId}]` | owner/manager | Sharing |
| GET `/api/nodes/{id}/file` · `…/preview` · GET `/api/nodes/{folder\|root:<id>\|mine}/zip` · GET `/api/zip?ids=` | viewer | Download, image preview (§3.3), a folder / shared folder / My docs as a streamed .zip, several items in one .zip (§9.4) |
| POST `/api/nodes/{folder\|root:<id>\|mine}/upload?name=&onClash=keep\|replace` | editor of the folder | One file; the body is the file (§9.4) |
| GET `/api/docs/{id}/versions/{n}/file` | viewer | One kept version (or a replaced file's previous copy) as a download |
| GET `/api/docs/{id}/export?format=xlsx\|csv&tab=&mode=values\|formulas` · POST `/api/import?name=&parentId=&format=` | viewer · editor of the folder | Export a sheet / import a `.csv` or `.xlsx` into a new sheet (§8.5) |
| POST `/api/docs/{id}/convert` · POST `/api/docs/{id}/edit-copy` | editor · viewer | A `.csv` sheet → `.xlsx` · *Save a copy to edit* (a read-only `.xlsx`) |
| GET `/api/shared-folders` | access | Admin shared folders you can use `{rootId, label, mode, exists}` (missing ones only for admins) |
| GET `/api/search?…` · GET `/api/search/export?…` | access | §10 (parameters §10.8) |
| GET / POST `/api/searches` · PATCH / DELETE `/api/searches/{id}` · GET / POST / DELETE `/api/searches/recent` | access | Saved searches (`{name, q, filters, pinned}`; PATCH `{name?, pinned?}`) and the recent 20 |
| GET `/api/admin/users` · PATCH `/api/admin/users/{id}` (`disabled`, `folder`) · POST `…/docs/delete` · notify routes | admin | People (counts and sizes only) |
| GET `/api/admin/share-dirs?path=` · GET / POST `/api/admin/shared-folders` · POST `…/inspect` · PATCH / DELETE `…/{id}` · POST `…/{id}/rescan` | admin | Shared folders: `{path, label, access: {personId \| "*": none\|ro\|rw}}`; PATCH `{label?, access?}` (access replaces the list); the browser marks folders already shared; counts and sizes only |
| GET `/api/admin/docs-folder` · POST `/api/admin/docs-folder/inspect` · POST `/api/admin/docs-folder/choose` · POST `/api/admin/rescan` | admin | Status, free space, totals · the checks for a typed path (no changes) · first-run choice (§5.6) · Rescan now |
| POST `/api/admin/docs-folder/read-only` (`{on, target?}`) · POST `…/target` (`{path}`: checks + copy commands) · POST `…/check` (`{path, deep}`, a background job) · GET `…/check/{job}` · GET `…/check/{job}/report.csv` · POST `…/switch` (`{path, jobId, force?, confirm?}`) | admin | Moving to a new location (§5.7) |
| GET `/api/tags` · GET / PUT / POST `/api/nodes/{id}/tags` | access · viewer · editor | Tags with counts (what you can open) · an item's tags and colour; PUT `{tags, color?}` replaces, POST `{add?, remove?, color?}` (§17.1) |
| GET `/api/docs/{id}/links` | viewer | `{links: {text: {state: ok\|noaccess\|deleted\|missing, id?, name?, kind?}}, backlinks}` (§17.3); a note's PATCH may carry `links: {text: id}` (the picker's choices) |
| GET / POST `/api/docs/{id}/pdf` | viewer · editor of its folder | Download as PDF (`X-Docs-Unsupported`: characters shown as "?") · Save PDF here (§17.12) |
| GET / PUT `/api/me/pins` · POST `/api/nodes/{id}/pin` (`{value, cell?}`) | access | Pins with previews · their order · pin / unpin (§17.13) |
| POST `/api/quick-note` · POST `/api/quick-note/{id}/finish` · GET `/quick-note` | access · editor · — | Make a quick note · name it after its first line (or Trash it when empty) · the app page for a dashboard button |
| POST `/api/docs/{id}/convert` | editor | also a note: Plain ↔ Markdown (renames `.txt` ↔ `.md`) |
| GET `/api/activity?person=&place=&type=&before=&limit=` · GET / POST `/api/follows` · DELETE `/api/follows/{target}` | access | 🕑 Activity (§17.4: `person` = a person's id, `me`, `others`, `outside`; `place` = a folder, `root:<id>`, `mine`, `shared`, `folders`; `type` = note / checklist / sheet / folder / file; paging by `before`) · follows `{target}` (a node id or `root:<id>`) |
| GET / PUT / DELETE `/api/nodes/{id \| root:<id>}/ha-sensor` | viewer · editor · editor | §17.9: the sensor, turning it on (`{name, cell, unit, hideItems}`) or off |
| GET `/api/reports/storage?place=mine\|root:<id>&years=` · POST `/api/reports/duplicates/keep` (`{keepId}`) · GET `/api/admin/storage` · POST `/api/admin/storage/empty-trash` | access (own folder, rw shared folders) · editor of the copies · admin · admin | §17.8 |
| POST `/api/nodes/{folder \| root:<id> \| mine}/scan?name=&type=pdf\|jpg` | editor of the folder | §17.7: one scan (the body is the PDF or the JPEG) |
| POST `/api/import/{keep\|zip}/upload?name=` · POST `/api/import/{keep\|zip}/{token}` (`{parentId}` for a .zip) · GET `/api/import/jobs/{id}` | access | §17.11: upload and preview · start · progress |
| GET / PUT `/api/nodes/{id}/text` | viewer · editor | a file's searchable text: the PDF's status, text read by AI (editable) |
| GET `/api/ai` · POST `/api/ai/ocr` `{id}` · `/api/ai/summarise` `{id}` · `/api/ai/to-checklist` `{text \| id}` · `/api/ai/to-sheet` `{text \| image}` · `/api/ai/ask` `{folder, question}` · GET / PUT `/api/ai/folder/{ref}` `{on}` | access (OCR and the folder switch: editors) | §17.6; 409 while AI is off or not set up (nothing is sent) |
| POST `/api/admin/ai/test` · GET `/api/admin/ai/usage?days=` | admin | Test connection · Admin → AI usage |
| POST `/api/nodes/{id}/chat/chats` (`{chatId?}`) · POST `/api/nodes/{id}/chat/card` (`{chatId, share?: viewer\|editor, members?: <members request id>, memberIds?: [ids]}`) · GET `/api/bus/requests/{id}` | viewer (members' access: owner/manager; never children) · the person who asked | §17.15: ask Chat for your chats (answered within 2 minutes or not at all) — with `chatId`, who is in that one chat; post a card; the request's state, answer or error in plain words |
| POST `/api/docs/{id}/todo/lists` · POST `/api/docs/{id}/todo` (`{listId \| new: {name, shared}, keys?, includeTicked, move}`) | viewer (Move: editor) | §17.16: ask Todo for your lists; send items (several messages when long) |
| GET `/api/admin/connected-apps` | admin | APP_MESSAGES_SPEC §5 |
| GET `/api/templates` · POST `/api/templates/use` (`{ref, name, parentId}`) · POST `/api/docs/{id}/save-template` (`{name, household}`) · DELETE `/api/templates/{ref}` | access · editor of the folder · viewer (household: admins) · yours / admins | §17.17 |
| GET `/api/rules/{ref}` · POST `…/filing` · PUT / DELETE `…/filing/{rule}` · POST `…/filing/order` · POST `…/filing/dry-run` · PUT / DELETE `…/cleanup` · POST `/api/filing/{log}/undo` · POST `/api/ai/suggest-filing` (`{id}`) | viewer · editor of the folder (rw in a shared folder) · editor of the file | §17.18–§17.19 (`ref`: a folder id or `root:<id>`) |
| POST `/api/nodes/{folder \| root:<id> \| mine}/seen` | viewer | §17.21 Mark all as seen; `is:new` in search; items carry `changed`, an open document `changedSince` |
| GET `/doc/{id}` · `/folder/{id}` · `/file/{id}` | — | "Open in Docs" sub-paths: a relative redirect to `../#/doc/<id>` … (APP_MESSAGES_SPEC §6.5) |
| PATCH `/api/admin/users/{id}` (`isChild`, `parents`) · `/api/admin/shared-folders` (`kids`) | admin | §17.20 |
| GET / PUT `/api/admin/settings` · GET `/api/admin/backup` · GET `/api/admin/backup/size` · POST `/api/admin/restore[?replaceFiles=true]` (the zip as the body) | admin | App settings (`docs_path` refused here) · backup, its size before downloading, and restore (§13.1) |

## 13. Frontend
- **Themes** from `common/` (one set of names across the apps, per the shared-code plan); shared `theme` /
  `sidebarCollapsed` keys.
- **Sidebar** (§6.1) with ➕ **New** (Note / Markdown note / Checklist / Sheet / Folder / Upload / Scan / Import);
  phones get a bottom bar: Docs · Search · ➕ · Shared · More.
- **Folder view:** breadcrumb, list or grid, sort, multi-select → Move, Copy, Share, Download, Tag, Delete; drag and
  drop on a computer (also files from the computer → upload). Files added outside the app show "added outside the app".
- **Editors:** §1 and §8; header with name, save state ("Saved" / "Saving…" / "Offline — will retry"), Share, ⋯
  (History, Export, Copy, Move, Show where it's stored — the path under `/share`, Delete).
  *(R2)* The header is **one line** for every kind (note, Markdown note, checklist, sheet), at every width: **←** · the
  name (takes the rest of the line, cut with "…" when long; its tooltip is the full name, and it is still the Rename
  button for editors — Rename is also in ⋯) · **Share** (only for people who may share; no gap when it's absent) ·
  **⋯** ("More actions", the same menu as before, which also holds Share…). Under it one small line: the role chip
  for someone else's document, where / who / when (cut with "…"), "X is editing", the save state. Keyboard order
  ← · name · Share · ⋯. On a phone (≤ 760 px) the freed height goes to the editor: a note's text box reaches down to
  the bottom bar (never shorter than before; refitted when the width changes, not when the keyboard opens) and the
  sheet's grid is 46 px taller (`100dvh − 374px`). Measured with the same documents (editor area visible above the
  bottom bar, before → after): 390×844 note 466 → 575, checklist 575 → 621, sheet 424 → 470, a viewer's note 466 →
  611; 360×640 note 262 → 371, sheet 220 → 266. Desktop is the same one line (no change in height). Uploaded files
  (PDFs, pictures…) open in the file panel, not the editor, and are unchanged.
- **Search page** (§10). *(built, step 4)* The box (with a **?** of tips), a panel "Where to look, how to match,
  filters" (open on computers, folded on phones), the chips, then the results as a table on computers (name,
  location, modified, by, size, type, ⋯) and rows on phones, with tick boxes and the selection bar, sort / direction
  / group, ☆ Save search, ⬆ CSV, pages; with nothing typed it shows saved and recent searches. ⋯ on a result adds
  *Show in folder* (the folder opens with the item flashing); ⋯ on a folder adds *Search in this folder*.
- **Back gesture** with `backnav.js`; the start page is the home page (pins, recent, My docs).
- **"How the app sees you"**, the "No admin yet" banner, the HTTP warning, and a "Documents folder not found" banner
  (§5.6).
- **Admin:** People (access switch, folder name, doc count and size, 🔔 phones, delete docs), Shared folders, Documents
  folder (location, status, free space, *Change location…* with read-only mode, copy check and switch — §5.6–5.7), App settings, Storage (§17.8), AI usage (§17.6), Backup and restore.
- Narrow screens checked at 360, 412, 768 and 1280 px.
- *(built, step 6)* Find & Replace in notes (a bar: next / previous, match case, Replace, All; Ctrl+F / Ctrl+H),
  Find in checklists (filters the items) and in sheets (🔍 / Ctrl+F: cells on every tab by what they show or hold;
  a result selects the cell). Phones: swipe a checklist item sideways (≥ 64 px) to tick or untick it.
  *Decision*: no pull-to-refresh — none of the sibling apps has one, and open documents already check every
  10 seconds. The read-only banner (§5.7) shows on every page.
- *(built)* Phones (≤ 760 px) get the bottom bar instead of the sidebar; **More** is a page with the other spaces,
  Settings, Admin and the theme. Folder view: a list sorted by name / modified / size (folders first; the choice is
  the person's `sort` setting) with a ⋯ menu per item. *(built, step 3)* **List or grid** (▦ / ☰, per device;
  the grid shows thumbnails of real images ≤ 3 MB), **tick boxes** on every item and the **selection bar** (Move,
  Copy to My docs, Share, Download (.zip), Favourite, Delete — each shown when every ticked item allows it), **drag
  and drop** on a computer (items onto a folder row, tile or breadcrumb to move them; files from the computer onto
  the list or a folder to upload), **⬆ Upload** (and ➕ New → Upload files) with a progress panel, the **file
  panel** (preview, details, Download, Replace with a new copy), **📁 Shared folders** in the sidebar (when you
  have any) and **🔎 Saved searches** with the pinned ones under it. Each item's role chip is left out where it's
  the folder's own (the folder's head says "🔒 Read only" / "✏️ Read and write" in shared folders). Editors: notes (Wrap / Monospace per device, autosave, 10-second check, conflict dialog with a line
  compare) and checklists (tick, edit in place, ↑ ↓, indent, delete, add, Hide ticked, Untick all). The secret hint
  sits under both editors. *(built, step 5)* Sheets: the grid editor of §8.5 (the secret hint sits under it too,
  checking the cells' text). Turned-off people see only the message and How the app sees you.

### 13.1 Backup and restore
- **HA backups** of the app hold `/data` (the database). The documents are in `/share`: in HA backups when *Share* is
  ticked. DOCS.md says so on the first page.
- **Admin → Backup:** a zip with a consistent `docs.db` snapshot, plus the docs folder's files when `backup_files` is on
  (streamed; size shown first).
  *(built)* With `backup_files` the zip holds `files/people/…`, `files/.trash/…` and `files/.versions/…` (never the
  marker or `.tmp`). A restore keeps this install's `install_id`, `docs_confirmed` and `docs_path` (the backup's are
  dropped) and re-points the person roots at the current documents folder. The zip is built in a temp file and
  then sent. *(built, step 3)* The Backup tab shows the size first (`GET /api/admin/backup/size`: the database, and
  with `backup_files` the number and size of the files the zip will hold).
- **Restore:** validates the zip, swaps the database, re-runs migrations, then **re-scans every root** — the index is
  rebuilt from what's on disk, and shares/tags for files that are gone are kept for 30 days in case the files come
  back (e.g. Share restored afterwards). Files in the zip, if any, are restored into the docs folder only when it's
  empty or the admin ticks *Replace files*.
- *(security review 2026-10)* A backup with `backup_files` holds everyone's documents and a restored `docs.db` decides who may open what:
  both are admin powers by nature (whoever administers Home Assistant runs the host). DOCS.md says so; downloads and
  restores are audited.

## 14. Invariants
- Every document and upload is a file under `/share`; the database is never the only copy of document content.
- The app never copies, moves or deletes the documents folder as a whole; moving it is the admin's copy plus the app's
  check and switch (§5.7). The old folder is never changed except its marker's `moved_to`.
- While read-only mode is on, nothing is written to the documents folder by anyone, background jobs included.
- Every write is write-to-temp-then-rename in the same file system, under a per-file lock, with an etag check.
- Nothing user-written is parsed as HTML; files are never served inline except checked raster images.
- Every path is resolved and `realpath`-checked under its root on every request; admin shared folders can't overlap
  the docs folder.
- Access is checked inside every query, search included; 404 for anything you can't see; admins get no content access
  through the app's pages (what Home Assistant admins can do on the host is said plainly, §3.2).
- Nothing crosses owners by itself: rename matching stays inside a root (§5.5); Trash is its owner's (§7.4).
- Files are opened once and checked where they really are (§3.3); request bodies are counted while read.
- `.xlsx` files the app can't fully keep are never overwritten.
- Regex runs only in the time-limited worker.
- `config.yaml` keeps only `admin_users` as an option; the only `map:` entry is `share` (read-write).

## 15. Build plan

| Step | Version | What ships |
|---|---|---|
| **1. App setup, notes and checklists** | 1.0.0 | App skeleton, people from HA, admin, App settings, choosing the documents folder (§5.6: first-run card, folder browser, checks, marker, people folders), the index, notes (`.txt`) and checklists (`.md`), file writes and conflicts, versions, trash, favourites, recent, basic search. |
| **2. Sharing** | 1.1.0 | People / Everyone, roles, inherited shares, Shared with me, transfer, leave/hide, notifications. |
| **3. Admin shared folders** *(built)* | 1.2.0 | Admin → Shared folders, ro/rw, uploads, previews, zip, overlap rules; with the step-1 leftovers (uploads into My docs, multi-select, drag and drop, grid view, backup size). |
| **4. Search** *(built)* | 1.3.0 | The Search page: scopes, name modes, wildcards, regex worker, filters, query syntax, sorting, grouping, bulk actions, saved and recent searches, CSV of results. |
| **5. Sheets** *(built)* | 1.4.0 | Grid, formats, formulas, totals, status-bar stats, `.xlsx` (openpyxl) and `.csv`, read-only rule for foreign `.xlsx`, print. |
| **6. Polish** *(built)* | 1.5.0 | Moving to a new location (§5.7: read-only mode, copy check and report, switch, switch back), secret hint, Find & Replace, phone gestures, README/DOCS, repository README row. |
| **7. Organise** *(built)* | 1.6.0 | Tags and colours, Markdown notes, links and backlinks, pins and Quick note, print / PDF. |
| **8. Stay up to date** *(built)* | 1.7.0 | Activity feed and follows, HA sensors, storage report. |
| **9. Bring things in** *(built)* | 1.8.0 | PDF text search, camera scan, Google Keep and zip import. |
| **10. Sheets 2** *(built, with step 5)* | 1.9.0 | More functions, tabs, charts, conditional colours. |
| **11. AI** *(built)* | 1.10.0 | Optional AI: OCR, summaries, text → checklist/sheet, ask a folder. |
| **12. Connect and tidy** *(built)* | 1.11.0 | Send to Chat and checklist → Todo over the app bus (§17.15–17.16), templates (§17.17), filing and clean-up rules (§17.18–17.19), Kids' space (§17.20), "what changed since you last looked" (§17.21). |

**Tests:** access for every role and for turned-off people (404); inherited shares including files added outside the
app; checklist ops from two people; 409 paths for app edits and outside edits; versions on outside changes; trash and
restore with name clashes; the index (new, changed, renamed by inode, deleted, gone and back); marker and docs-folder
checks (other install, overlap with shared folders and Chat, read-only, missing mount, files without a marker); the
move: read-only mode blocks every write and background job, the copy check finds missing / different / extra files
(quick and deep), switch keeps node ids, shares, tags and history by relative path, the old folder untouched apart
from `moved_to`, switch back; write-then-rename and locks;
`.xlsx` round trips (formulas, formats, tabs, charts) and the read-only rule for foreign files; `.csv` dialects;
search (every name mode, wildcard translation, regex timeout, filters, sorting, access inside search); path attacks
(`..`, absolute paths, symlinks, swapped folders, Windows names); preview/download headers; zip bombs and XML entities
in `.xlsx`; `sheetcalc.test.js`; packaging (`map` exactly `share`, port, version, CHANGELOG, no personal text).

## 16. Decisions (were open questions; settled 2026-10-04 with the drafted defaults)
1. Panel title **Docs**, icon `mdi:file-document-multiple`.
2. Sharing with Everyone defaults to **Can view** (`everyone_default_role: viewer`).
3. Checklists: **viewers may tick** items by default (per share, `viewers_tick`).
4. Person folders are named by **display name** ("Priya"; `folder_names: display`).
5. New sheets are **`.xlsx`** (a personal setting can switch to `.csv`).

## 17. Extras

### 17.1 Tags and colours
- Any number of **tags** per file or folder (`taxes`, `car`, `school`), typed with suggestions; one optional **colour**
  per item (8 colours, a dot and a tinted icon). Stored in the database by node id, so they follow renames and moves
  (inside and, by inode, outside the app).
- Visible to everyone who can see the item; editors change them. Sidebar **🏷 Tags** with counts. Search `tag:`,
  `-tag:`, `color:`; Tag and Colour filters on the Search page.
- Schema: `node_tags(node_id, tag)`; `nodes.color`.
- *(built, step 7 — `tags.py`)* `node_tags(node_id, tag, tag_folded, added_by, added_at)`, one row per tag
  whatever its case (the first spelling used anywhere wins — *(security review 2026-10)* anywhere *the person can open*: a spelling is
  never borrowed from items they can't open, which would say the tag exists there). A tag is 1–40 characters, trimmed, a leading `#`
  dropped, no quotes or commas; at most 20 per item. Colours (*decision*: 8): red, orange, yellow, green, teal,
  blue, purple, grey — a dot on the icon and a soft tint. Viewers see them, editors change them (not in
  read-only mode). Lists, the editor and search results carry `tags` and `color`. Search: `tag:` (`-tag:`,
  `tag:"two words"`, the Tag filter: comma-separated) and `color:` (an unknown colour is an error chip). The
  sidebar's 🏷 Tags shows the 8 most used (from `/api/me` → `tags`, counted over what the person can open);
  `#/tags` lists them all with the colours. ⋯ → Tags and colour…; the selection bar's 🏷 Tag… adds / takes off
  tags and sets a colour on many items. Tags aren't copied by Make a copy.

### 17.2 Markdown notes
- A note is **Plain** (`.txt`) or **Markdown** (`.md`); *Convert* switches (renames the file). New notes follow the
  person's default.
- Supported: headings, **bold**, *italic*, `code`, code blocks, quotes, bullet and numbered lists, **inline
  checkboxes** (`- [ ]`, tick them in the preview), links, horizontal rules, simple tables.
- **Rendering builds DOM nodes** with `md.js`; no HTML is passed through (raw HTML shows as text), links only
  `http:`/`https:` and `[[doc links]]` (§17.3); images in Markdown aren't loaded.
- Edit / Preview / Split; a small toolbar (B, I, heading, list, checkbox, link).
- *(built, step 7)* `md.js` parses into plain blocks (headings, paragraphs, nested lists with tick boxes and their
  source line, quotes, fenced code, rules, pipe tables) and inline nodes (strong, em, code, links, `[[doc]]`,
  autolinks, images as "🖼 alt"), then builds DOM with `createElement` / `createTextNode` only — tested in Node
  with a recording fake document and a 3 000-input fuzz test (only whitelisted elements and attributes, `href`
  only `http(s)://` or `#/doc|folder/<id>`). Ticking in the preview flips that line's `[ ]` / `[x]` and saves. Split
  is the default on computers, Edit on phones, Preview for viewers (remembered per device). Ctrl+B / Ctrl+I.
  *Decision*: a Markdown note saved in the app stays a Markdown note even when it's only tick boxes for now; only
  a change made outside the app is classified again (§5.2). *Convert* (⋯ → Make it a Markdown note / plain text,
  `POST /api/docs/{id}/convert`) renames the file, keeping the item; a note that's only tick boxes isn't
  converted to `.md` (it would be a checklist).

### 17.3 Links between documents
- Type `[[` to pick a file or folder you can see. Written into the file as `[[Title]]`-style text with the node id
  kept in the database (`node_links(from_id, to_id, text)`), so the file stays readable elsewhere and links survive
  renames. Re-linked by title if the database link is lost.
- **Linked from** (backlinks) on each document — only from documents the viewer can open. A link the viewer can't open
  shows "🔒 No access"; a deleted target shows "Deleted".
- *(built, step 7 — `links.py`)* `node_links(from_id, text, to_id)`; links in plain notes and Markdown notes
  (`[[text]]`, no `|`). Rows are brought up to date on every app save, on creating a note with text, on a
  version restore, and when an editor opens a note (a file changed outside). New texts take the picker's id (if
  the writer can open it), else a title lookup among what the writer can open (name with or without its
  extension, the same space first). A row whose target was purged is looked up again; a target in Trash stays
  linked ("Deleted" until restored). Readers get "missing" (*Not found*) for text that matches nothing. Plain
  notes list their web links and `[[links]]` under the editor (tappable); every note shows *Linked from*.

### 17.4 Activity feed and following
- **🕑 Activity**: what changed in everything shared with you and your shared folders — created, edited (grouped:
  "Alex edited 'Budget' 4 times"), renamed, moved, deleted, shared with you, and changes found by the index
  ("changed outside the app"). Filters: person, place, type; last `activity_days`.
- **Follow** a document, folder or shared folder: a phone notification for new files and changes, batched (at most one
  per item per 15 minutes; "3 new files in House papers › Taxes"), held during the person's quiet hours (default
  22:00–07:00). Your own changes never notify you.
- Schema: `activity(id, actor_id, node_id, root_id, action, detail, created_at)` (no contents), `follows(user_id,
  node_id, created_at)`.
- *(built, step 8 — `activity.py`, `follows.py`)*
  - **Recorded** where the change happens: created (new documents and folders, uploads, copies, scans, imports),
    edited (note / checklist / sheet saves, a replaced upload, a version restore), renamed (also Plain ↔ Markdown,
    .csv → .xlsx, Quick note naming; `detail {from, to}`), moved (`{from, to}` folder names; a transfer is a move to
    "<name>'s folder"), deleted, restored, shared (`target_user`: only that person — or, for Everyone, everyone who
    can open it — sees the row). `activity` also keeps `parent_id` (the folder it was in), `count` and `last_at`.
    **The index** reports what it finds on disk with `actor_id` NULL ("Someone outside the app"): new, changed,
    renamed / moved (matched by inode), deleted — never on the first scan of a root (*decision*: that is everything
    already there), at most 100 per scan of a root.
  - **Edits are grouped** (*decision*): a save by the same person on the same item as its latest row, on the same day
    in Home Assistant's time zone, updates that row — within 10 minutes it is the same editing session (only the time
    moves on), later it counts one more edit ("edited 'Budget' 4 times"). In the feed, adjacent rows of the same
    person adding (or deleting) things in the same folder within 10 minutes show as one entry with *Show them*.
  - **The feed** joins the access CTE: a row shows while its item is something the person can open — a deleted item
    keeps its row (and parent) while in Trash, so it shows to the owner of that Trash (and, in an admin shared
    folder, people with Read and write — §7.4); rows of items purged for good,
    and rows older than `activity_days`, are removed hourly. *Decision*: your own changes are listed too ("You"),
    with the *Who* filter (everyone, everyone but me, me, outside the app, a person).
  - **Follows** (`follows(user_id, target, created_at, cursor, last_sent_at)`; target a node id or `root:<id>`, at most
    200 per person): `dispatch()` every minute takes the Activity rows after the follow's `cursor` on the item, inside
    a followed folder (by node or by the folder it was in) or in a followed shared folder — others' changes only, joined
    with the access CTE, never "shared" rows. It sends when the newest of them is at least 60 seconds old (a burst ends),
    the last notification for that follow is 15 minutes old or more, and it isn't the person's quiet time; then the
    cursor moves on. Messages: one change — "Alex edited “Budget.txt”" (with "(4 times)"), "Alex added “Bill.pdf” to
    House papers", renamed / moved / deleted / restored; several new files — "3 new files in House papers › Taxes";
    otherwise "5 changes in Trip — by Alex, Priya". Turned off (`notifyFollows`) or turned-off people: passed over (the
    cursor moves). The deletion of a followed item is told to its owner only (*(security review 2026-10)*: in Trash it is the owner's
    alone, §7.4). Phones and notify services as in §11.
  - *(security review 2026-10)* **Details as the reader may know them** (`activity.feed_detail`, used by the feed and follow messages): a
    move's `from` / `to` folder names show only for folders the reader can open (`fromId` / `toId` are kept with the
    row; "" = the top); a rename's (and a filing rule's) old name only to people who could open the item when it was
    renamed (`acl`, who could open it then, kept with the row, never sent); a filing rule's folders likewise; the
    owner sees everything. A followed folder's label names only folders above it the reader can open.

### 17.5 PDF text search
- `pypdf` extracts text from PDFs anywhere you can see, in a background job, up to `pdf_index_mb` (20 MB) and 500 pages
  per file. Encrypted PDFs are skipped ("password-protected"); PDFs with no text are marked "scanned — no text" (AI can
  read them, §17.6).
- Text goes into `fts`; snippets show the page ("page 3"). PDFs still download rather than preview inline (§3.3).
- *(built, step 9 — `pdftext.py`, `formats/pdf_worker.py`)* A job every minute (in a worker thread, ≤ 5 PDFs and ≤ 40 s
  a run) reads each live PDF whose content (SHA-256) hasn't been read: the file is read after the realpath check
  without following links (≤ `pdf_index_mb`; bigger ones are `too_big`) and handed to `python -I pdf_worker.py`,
  which limits itself (1 GB address space, 60 s CPU, no file writes, 64 open files) and is killed after 30 s wall
  time; one at a time. Results in `pdf_text(node_id, sha256, status, pages, text, page_lines)`: `ok`, `encrypted`
  ("Password-protected"; one that opens with an empty password is read), `scanned` ("Scanned — no text": fewer than
  3 letters on all pages together), `too_big`, `failed` (a broken or hostile file — garbage, reference loops, deep
  nesting, a stream that inflates without end). The text (≤ 500 pages, ≤ 2 000 000 characters) goes into `fts` with
  `nodes.pdf_pages` (the line where each page starts), so a result has `page` and the snippet "page 3: …". The
  index leaves such files' text empty when it re-indexes them; the job puts the derived text back (`restore_bodies`).
  A scan saved through §17.7 is read straight away. Items carry `pdfText` / `pdfPages`; the file panel shows the
  status (`GET /api/nodes/{id}/text`).

### 17.6 Optional AI
- **Off until an admin sets it up** in Admin → App settings → *AI*: provider (your own Ollama, an OpenAI-compatible
  service, or Anthropic Claude), URL, access key, text model, vision model, monthly limit. Same `ai_client.py` /
  `ai_usage.py` and AI-usage page as the other AI apps.
- **Read text from images and scanned PDFs** (OCR): opt-in per folder ("Read text from scans in this folder"); the
  text goes into search, shown in the file panel as "Text read by AI" (editable). Saved in the database, never written
  into the file.
- **Summarise** a note or a PDF (a panel; *Save as note* writes a `.md` beside it).
- **Turn text into a checklist** (a recipe, an email) or **a table into a sheet** (pasted text or a photo of a table).
- **Ask about a folder**: a question over the documents in one folder you choose (at most 30 files / 200 000
  characters); the answer lists the files it used, each linked.
- **Privacy:** nothing is sent until AI is set up; the dialog names the provider before sending; only what you picked
  is sent; DOCS.md lists what each action sends. Per-person switch to hide AI buttons.
- *(built, step 11 — `ai_client.py`, `ai_usage.py`, `ai.py`, `static/ai.js`)*
  - **Settings** as §4 (*built*). `settings.ai_problem()` says why AI can't be used (off, no model, Ollama without an
    address, Claude without a key); every AI route answers 409 with it **before anything is sent**, and the folder
    job does nothing. `/api/me` → `ai {on, provider, address, model, visionModel}` (all null while off).
  - **The client** is the shared `ai_client` (retries, fall-backs, errors with the key scrubbed). The shared client
    labels every picture `image/png`; the app's `_post` hook gives each picture its real type from its first bytes
    (JPEG scans, GIF / WebP photos) before the request leaves. Pictures go to the vision model (else the text model).
  - **Usage** (`ai_calls(at, purpose, provider, model, ok, error, tokens_in, tokens_out, ms)`; purposes ocr, summarise,
    checklist, sheet, ask, test — no people, no content), Admin → AI usage like the other AI apps (today / 7 / 30 days /
    all time, by day, by action, by model, latest 25, cost when prices are set, this month against the limit). Past
    the monthly token limit every action answers 429.
  - **Read text** (OCR): editors of the file (*decision*: the text is shared with everyone who can see the file, so
    only people who can change it start it). A picture (≤ 8 MB, a real PNG / JPEG / GIF / WebP) or a scanned PDF's
    JPEG page pictures (taken out by `pdf_worker.py` in `images` mode, ≤ 10 pages, one request each; *decision*: the
    app has no PDF renderer, so only JPEG scans — what phones and §17.7 make — can be read). Kept in
    `ocr_text(node_id, sha256, text, model, edited_by)`, put into `fts`, shown as "Text read by AI" (editors correct
    it: `PUT /api/nodes/{id}/text`). **Per folder** (`ai_folders(target)`; *(security review 2026-10)* switched **on** only by the
    folder's owner or a manager in a person's folder — never an editor, since its pictures go to the AI provider —
    or by people with Read and write in an admin shared folder; switched off by anyone who can change it): the background job reads up
    to 3 pictures or scanned PDFs a minute in that folder and the folders inside it — those already there too
    (*decision*; the dialog says how many) — skipping files it already read (same content) or failed on.
  - **Summarise** (viewers): a note's / checklist's text, a PDF's text or text read by AI (≤ 100 000 characters) →
    bullet points shown in a panel; *Save as note* makes `<title> — summary.md` beside it (or in My docs).
    **Checklist from text** (pasted text or a note) and **table → sheet** (pasted text or one photo, made ≤ 1 600 px
    in the browser) ask for JSON (`{"items"}` ≤ 200 / `{"rows"}` ≤ 200 × 26) the person checks; the checklist is made
    through §12's create + `add` ops, the sheet through the `.csv` import. **Ask about a folder**: up to 30 files in
    the folder and the folders inside it that the person can open (the folder's own files first, then the newest),
    their indexed text, ≤ 200 000 characters in all, each as "[n] name"; the answer's [n] marks are the files it
    used (`files`), `sent` lists every file that was sent.

### 17.7 Phone camera scan
- **📷 Scan** in ➕ New and in any folder you can edit: the phone camera (`<input type="file" accept="image/*"
  capture="environment">`), several pages in a row, crop/straighten (corner handles), black & white option, then save
  as **JPEG images** or **one PDF** (built in the browser by a small PDF writer in the app, JPEG pages only).
- **Auto-name** `Scan 2026-10-03 20-41.pdf` (editable; a pattern in Settings, e.g. `{date} {folder}`). Saved as a file
  in the folder you're in.
- With AI on, *Read text* runs straight after (§17.6).
- *(built, step 9 — `static/scanpdf.js`, `static/bring.js`)* ➕ New → 📷 Scan opens the camera straight away (a file
  input with `capture="environment"`; on computers, several pictures can be chosen). Each photo is drawn on a canvas
  (from a `blob:` URL, allowed by the CSP) at most 2 000 px on its longest side; four corner handles (pointer events,
  arrow keys too) mark the page; ↻ Rotate, Whole photo. Saving straightens each page with a homography (bilinear,
  in the browser, the size from the corners' distances), *Black and white* is an adaptive threshold (each pixel
  against 88 % of its neighbourhood's mean, from an integral image), and the page is a JPEG (quality 0.85, 0.8 in
  black and white). **One PDF** is written by `scanpdf.js` (a page per JPEG, `/DCTDecode`, A4 wide and as tall as
  the photo's shape, a correct cross-reference table; Node-tested and read back with pypdf). **JPEG pictures**:
  one file per page ("<name> 1.jpg" …). Saved through `POST /api/nodes/{place}/scan?name=&type=pdf|jpg` (*decision*,
  instead of `/api/scan`: the same place names as uploads; the body must really start like a PDF / JPEG, the name
  gets that extension, " (2)" on a clash, the upload limit and quota apply). The name pattern is the person's
  `scanName` (`{date}`, `{time}` in the device's time, `{folder}`, `{n}`), editable before saving.

### 17.8 Storage report
- **Admin → Storage:** per person folder and per shared folder: total size and count, **largest files**,
  **duplicates** (same SHA-256, every location; *Keep one* moves the others to Trash), **old files** not changed in N
  years, **empty folders**, files by type (a bar chart), growth over 12 months (from index history), and the space
  used by `.versions` and `.trash` (*Empty all Trash now*). Plus `/data` use.
- **People** see the same report for their own folder and for shared folders where they have read-write access.
- Shown against `quota_gb` when set.
- *(built, step 8 — `storage_report.py`)* Per root: files, bytes, folders (live index rows); by type (§10.8's type
  labels); History = `versions` rows of its nodes, Trash = files in it with a `trash_id` and its `trash` items;
  growth = the last `storage_daily` snapshot of each of the last 12 months (written once a day by housekeeping),
  the current month from now, months before the first snapshot estimated from `nodes.ctime` of what is still there
  (marked); duplicates = files with the same SHA-256 and size (> 0) with a copy in this root — **every copy the
  person can open**, anywhere; *(security review 2026-10)* the summary counts (`duplicates {groups, extraBytes}`) count only those
  copies too (a copy someone can't open never changes the numbers — no content oracle), and Admin → Storage counts
  duplicates within each folder only; *Keep one* trashes each other copy through the normal delete rules (copies they can
  only view are skipped and counted); largest 20; files not changed in N years (1–20, default 2; 50 listed); empty
  folders (100). *Decision* on Admin → Storage: admins get no content access as admins (§3.2), so Admin → Storage
  shows every root's totals, types, History, Trash, growth, quota and duplicate **counts and bytes** — never file
  names; the lists are on each person's own 💾 Storage page. `/data`: the database (with WAL) and the folder's
  total, free space. *Empty all Trash now* purges every Trash item of every root (refused in read-only mode).
  Bars and columns are drawn as elements whose widths / heights are set through the DOM (CSSOM, allowed by the
  CSP) in the theme's colours.

### 17.9 Home Assistant sensors (opt-in per item)
- ⋯ → **Show in Home Assistant** on:
  - a **checklist** → `sensor.docs_<slug>_open` (open items; attributes: total, done, the first 10 open items unless
    *Hide item text* is ticked);
  - a **sheet cell or range** → `sensor.docs_<slug>` (the value, unit, `state_class: measurement` for numbers — e.g.
    "budget left this month");
  - a **folder** → `sensor.docs_<slug>_files` (file count, newest file name and time).
- Published with `POST /api/states`, refreshed on every change (including changes found by the index) and re-posted
  every 5 minutes; removed when turned off or the item is deleted. Owners/editors only; everyone who can see the item
  is told it's on the dashboard. App setting `ha_sensors` (on). DOCS.md shows a recorder exclusion for private values.
- Schema: `ha_sensors(entity_id, kind, node_id, cell_ref, options, created_by, created_at)`.
- *(built, step 8 — `ha_sensors.py`)* `<slug>` comes from the name shown (the item's title unless changed): lower
  case, `[a-z0-9_]`, ≤ 40 characters; a clash adds `_2`, `_3`. `node_id` may be `root:<id>` (an admin shared
  folder's top; its editors). States: checklist — open items, `unit_of_measurement: items`, `state_class:
  measurement`, `total`, `done`, `open_items` (first 10, ≤ 100 characters each) unless `hideItems`; sheet — `cell_ref`
  `Tab!B4` (a formula's computed value) or `Tab!B2:B9` (*decision*: the sum of the numbers in it), numbers with the
  unit and `state_class: measurement`, text as it is (≤ 255, no state class), `unknown` when empty; folder — files at
  every level, `newest_file`, `newest_modified`. Each carries `friendly_name`, `icon`, `source: Household Docs`.
  🕑 Activity marks the item, its folders and its shared folder as changed (`activity.HOOKS`), so saves and changes
  the index finds post within one tick (15 s); everything is re-posted every 5 minutes and when the date changes
  (`sensor_publisher.Refresh`); a run stops after 3 failures in a row (Home Assistant away). A deleted item's
  sensors (and those inside a deleted folder) are removed; `ha_sensors` off removes every sensor from Home Assistant
  (the rows stay; on again posts them). At most 200 sensors. *(security review 2026-10)* Before every publish the person who turned the
  sensor on must still be able to open its item (turned on, and a role on it); otherwise the sensor is removed,
  its row deleted, the change audited and that person told ("“Medicines” isn't shown in Home Assistant any more:
  you can no longer open it") — so a checklist's items or a folder's newest file name never keep flowing to Home
  Assistant after its maker lost access. Items carry `haSensor` (the entity id) for everyone who
  can see them; the editor and the folder head say "📊 On the Home Assistant dashboard as …".

### 17.10 More for sheets
- **Functions:** `SUMIFS`, `COUNTIFS`, `AVERAGEIFS`, `VLOOKUP`, `HLOOKUP`, `XLOOKUP`, `INDEX`, `MATCH`, `IFERROR`,
  `MEDIAN`, `TEXT`, `DATE`, `YEAR`, `MONTH`, `DAY`, `EOMONTH`, `NETWORKDAYS`, `PMT`.
- **Tabs:** up to 10 per sheet, references across tabs; renaming a tab updates formulas. `.xlsx` only (`.csv` sheets
  offer *Convert to .xlsx*).
- **Charts:** bar, line and pie from a range, under the grid or on their own tab, redrawn on change (SVG in the app;
  written into the `.xlsx` as Excel charts).
- **Conditional colours:** greater/less than, between, text contains, date before/after, top/bottom N, duplicates —
  fill or text colour; written as Excel conditional formatting.
- *(built, with step 5 — §8.5)* All four. Tabs: ➕ adds one; tapping the current tab (or right-click) renames, moves or
  deletes it; a chart can have a tab of its own. Charts are added from a range (the selection) with a preview, and
  changed or removed from the chart's ⋯. Colour rules are listed per tab with the rule in words.

### 17.11 Import from Google Keep and folders
- **Google Keep:** upload the Keep folder from Google Takeout as a `.zip`. Each note becomes a `.txt`/`.md` note or a
  checklist `.md` (ticks kept) in **My docs → Google Keep**; title, created/edited times (set as file times),
  **labels → tags**, colour → colour, pinned → favourite, archived → a *Keep archive* subfolder, trashed notes skipped;
  attached images saved beside the note. A preview shows counts first; re-import skips notes already imported (Keep's
  id kept in the database).
- **A zip of files and folders:** unpacked into a folder you pick (zip-slip and size checks; names cleaned).
- *(built, step 9 — `imports.py`)* Two steps: `POST /api/import/{keep|zip}/upload` streams the .zip (≤ `upload_mb`)
  into the person's hidden `.tmp/`, checks it and answers a preview and a token (in memory, an hour);
  `POST /api/import/{kind}/{token}` starts a background job the page polls. **Checks**: ≤ 10 000 entries and ≤ 4 GB
  unpacked (by the central directory), a member is never read past its declared size; skipped and listed: encrypted
  members, links, absolute paths and drive letters, `..`, hidden entries and `__MACOSX`, names with nothing usable
  left after cleaning, deeper than 10, and members inflating more than 1 000 times past 10 MB ("a possible zip
  bomb"). **.zip**: *decision*: unpacked into a new folder named after the .zip (" (2)" on a clash) inside the chosen
  folder, so nothing is merged into existing folders; each file goes through the upload path (quota, names, index).
  **Keep**: the `Keep/*.json` notes of a Takeout (≤ 5 000, ≤ 2 MB each); *decision* on Keep's id: Takeout has none, so
  the note's `createdTimestampUsec` is used (else its JSON file name), kept per person in `keep_imports`. A list →
  checklist `.md` (`- [x]` / `- [ ]`), else a `.txt` note (*decision*: Keep notes are plain text); the title, else the
  first line (≤ 60), else "Keep note <date>"; the file's time is `userEditedTimestampUsec`, the item's created time
  `createdTimestampUsec`; labels → tags (labels a tag can't hold are skipped), colours → the 8 colours (Keep's pink →
  purple, brown → orange, cerulean / dark blue → blue, grey → grey), pinned → ⭐ Favourite, archived → *Keep archive*,
  trashed and empty notes skipped; attachments (by `filePath`, next to the JSON) are saved beside the note. The
  preview counts notes, lists, archived, pinned, attachments, labels, already imported, in Keep's bin and empty.

### 17.12 Print and save as PDF
- **Print** on notes, Markdown notes, checklists and sheets: a print-only layout (title, owner, date, checklists with
  empty boxes, sheets with gridlines and a repeated header row, a landscape hint for wide sheets). **Save as PDF** via
  the browser's print dialog.
- **Download as PDF** for notes and checklists, built by the app's small PDF writer (text, headings, boxes); *Save PDF
  here* writes it beside the document.
- *(built, step 7 — `docpdf.py`, `pdfwriter.py`)* Print: ⋯ → Print or save as PDF builds the print-only page
  (title, owner, changed / printed dates; checklists with ☐ / ☑; Markdown through `md.js`). The PDF writer is pure
  Python: A4, Helvetica (regular, bold, italic) and Courier, page numbers, drawn tick boxes. *Decision on fonts*:
  the PDF standard fonts with WinAnsi (Windows-1252) — nothing embedded, a few KB per page, every reader has
  them; characters outside Windows-1252 become "?" and are counted (`X-Docs-Unsupported`; the page suggests
  Print → Save as PDF, which uses the device's fonts). Embedding DejaVu Sans (permissive licence) would add
  ~700 KB per font to the image and need a TrueType subsetter. The width tables (`pdf_fonts.py`) come from
  Adobe's Core 14 AFM files, with their notice. *Save PDF here* goes through the upload path (the person must be
  able to add files to the document's folder; " (2)" on a clash).
- *(built for sheets, step 5 — §8.5)* 🖨 in the sheet editor (and Export → Print or save as PDF): the current tab with
  gridlines, the repeated header row, the totals row and its charts; the landscape hint for wide sheets.

### 17.13 Pinned docs and Quick note
- **📌 Pin** up to 8 documents or folders to your home page (reorder by dragging); cards show a preview (a checklist
  "3 of 12 done", a sheet's chosen cell).
- **⚡ Quick note**: a button on every page (and `N` on a keyboard) that creates `My docs/Inbox/Note YYYY-MM-DD
  HH-MM.txt` and opens it; the first line becomes the name when you leave. A URL hash `#quick-note` lets a Home
  Assistant dashboard button or a phone shortcut open it straight away.
- *(built, step 7 — `pins.py`)* Pins are `user_state.pinned_order` (+ `pin_cell` `{tab, ref}` for a sheet's card).
  At most 8 live pins the person can open (a deleted or no-longer-shared pin drops off the page and frees its
  place). Cards: a checklist's done / total with a bar, a note's first three lines (Markdown marks taken out), a
  folder's item count, a sheet's chosen cell (its value formatted in the browser with the person's number style,
  and the text in column A of that row as its label). Reorder by dragging ⠿ (pointer events, so phones too) →
  `PUT /api/me/pins`. ⋯ → Pin to home / Unpin; pinning a sheet asks for the cell (optional).
  Quick note: ⚡ in the top bar, `N` (not while typing or in an editor), `#quick-note`, and the page
  `/quick-note` (*decision*: Home Assistant's ingress panel passes a sub-path to the app but not a hash, so a
  dashboard button opens `/<full slug>/quick-note`, e.g. `/a0d7b954_household_docs/quick-note`; `/hassio/ingress/…`
  is a 404 in current Home Assistant). "Inbox" is reused when it exists (any
  case); the name uses Home Assistant's time zone, " (2)" when the minute is taken. Leaving it calls `finish`:
  while the name is still the quick one, it becomes the first line with something in it (Markdown marks and
  `[[ ]]` taken out, cut at a word to 60 characters, cleaned for Windows, " (2)" on a clash); an empty quick note
  goes to Trash (*decision*: so mis-taps don't pile up).

### 17.14 Settings, API and tests for the extras
- **App settings:** `pdf_index_mb` (20), `ai_*` (provider, URL, key, models, monthly limit — off by default),
  `ha_sensors` (on), `activity_days` (90), `keep_import` (on).
- **Personal settings:** follow notifications and quiet hours, show AI buttons, scan file-name pattern.
- **API:** `/api/tags`, `/api/nodes/{id}/tags`, `/api/docs/{id}/links`, `/api/activity`, `/api/follows`, `/api/ai/*`
  (summarise, to-checklist, to-sheet, ask, ocr), `/api/scan`, `/api/reports/storage`, `/api/nodes/{id}/ha-sensor`,
  `/api/import/keep`, `/api/import/zip`, `/api/docs/{id}/pdf`, `/api/me/pins`, `/api/quick-note`.
- *(built, steps 8, 9, 11)* Tests: `tests/test_activity.py` (recording and grouping, access in the feed, changes found
  by the index, filters, purge; follows: batching and the 15-minute rule, own changes, access, quiet hours with a
  fixed clock, a shared folder's top, a deleted item; sensors with the fake Home Assistant: entity ids, states and
  attributes, nothing re-sent unchanged, the 5-minute re-post, index-found changes, removal on off / delete / the
  switch, who may; the storage report's numbers, duplicates and Keep one, admin totals without names, Empty all
  Trash), `tests/test_bring.py` (PDF text with pages and search snippets, password-protected / scanned / too big /
  off, hostile PDFs and the worker's time limit; the scan PDF built by `scanpdf.js` in Node read back with pypdf and
  saved through /scan; an invented Takeout .zip, re-import; .zip attacks and limits), `tests/test_ai.py` (a fake
  provider recording every request: nothing sent while off or not set up, prompts carry only what DOCS.md says,
  picture types, the monthly limit, usage rows and Admin → AI usage, Test connection, reading text from a picture, a
  scanned PDF and the folder switch), `tests/js/scanpdf.test.js`.
- *(built, steps 6–7)* Tests: `tests/test_move.py` (read-only mode against every writer and the paused jobs, the
  check's missing / different / extra in quick and deep mode, capped lists and the CSV, the switch keeping ids,
  shares, favourites, tags, ticks, versions and Trash with the old folder untouched but its marker, typed
  confirmation, switch back), `tests/test_organise.py` (tags and colours incl. search and access, Markdown
  convert, links and backlinks with access, the PDF writer read back with pypdf, pins, Quick note naming),
  `tests/js/md.test.js` (incl. the fuzz test), `tests/js/numstyle.test.js`; tag searches joined the
  access-everywhere test.
- *(built, step 12)* Tests: `tests/test_connect.py` (the app's real bus on the fake Home Assistant event bus with an
  invented Chat and Todo in the process: chat lists and cards, members' shares only after the ack and only by owners
  and managers (and a manager demoted before the ack), nacks in plain words, a list not answered in time, nothing
  offered without the other apps or outside Home Assistant, children and read-only mode refused, Make a Todo list
  with keep / move, ticked items, sub-items, long checklists in parts one after the other, Move only after the ack,
  nacks leaving the items, no answer until the local expiry (one part or a later one: what was acked moved, nothing
  after it sent), Move checked again at the ack (a sender who can no longer edit: sent, nothing removed), every
  envelope free of document content, Connected apps, the page path, the Open in Docs redirect and a no-access
  answer that names nothing), `tests/test_connect_apps.py` (the real Household Chat
  and Household Todo from this repository in their own processes on the same fake bus: the card in the chat with
  its link and the member's share, a refused chat, a Todo list made, listed and moved into, someone Todo doesn't
  know; the repository's `tests/test_docs_connected_apps.py` runs it too),
  `tests/test_tidy.py` (templates built in, yours, the household's, path tricks, read-only mode; filing by upload
  and by the index, patterns and placeholders, first match wins, type and size, dry run, Undo from Activity and its
  7 days and rights, read-only mode holding arrivals, the maker's rights; clean-up to Trash and Archive with
  favourites and pins skipped, once a day, read-only mode; Kids' space — the route table walked as a child, the
  content checks, Kids folders, parents' view; what changed — dots, `is:new`, the note's old text, checklist items,
  sheet cells, your own saves, Mark all as seen), `tests/js/textutil.test.js` (`appLinkHash`).
- **Tests:** `md.js` never produces HTML from input (fuzzed); backlinks and activity respect access; follow batching
  and quiet hours; PDF extraction with sample and encrypted PDFs; AI with a fake provider (nothing sent while off); scan
  → valid PDF; duplicates; sensors with a fake HA; Keep import of an invented Takeout zip; tabs, charts and conditional
  colours in `.xlsx` round trips; every new function in `sheetcalc.test.js`.


### 17.15 Send to Chat (app bus)
- ⋯ → **Send to chat** on a document or folder: pick a chat you're in (from Chat's list, through the app bus,
  `APP_MESSAGES_SPEC.md`); Chat posts a card "<name> shared <title>" with an **Open in Docs** link.
- *Also give the chat's members access* (default off): adds a Docs share (Can view / Can edit) for each member — a
  normal share (§6.3), shown in Share…, removable there. **Changed (2026-10-04):** Chat can't know who may open a
  document, so the card itself never says "🔒 No access"; instead, opening an Open in Docs link (or any Docs link)
  to something you can't open shows Docs' own **🔒 No access** page ("This document isn't shared with you. Ask the
  person who sent it."). Like "🔒 No
  access" links (§17.3) and every 404 (§3.2), that page says nothing about the item — not its title, not its owner,
  not even whether it exists (a deleted item looks the same); the card in the chat already shows who shared it.
- Only offered when Chat is installed and says it can (`chat.card`); Chat checks you're allowed to post there. The card
  carries only the title, type, owner and a Docs id — never content.
- Needs `app_bus.py` + `ha_ws.py` in both apps and a new Chat message kind (`chat.card`), each with its own spec
  section and tests.
- *(built, step 12 — `app_messages.py`, `routers/connect.py`, `static/connect.js`)*
  - **The bus** starts in the lifespan (off without a Supervisor token): `hello` with `can: []` (Docs answers nothing)
    and `wants: [chat.card, chat.chats.list, todo.items.add, todo.lists.list]`; its own WebSocket; the outbox is run
    right after each send (a dialog is waiting) and every housekeeping tick (re-sends, expiry, the 6-hourly hello).
    **Admin → App settings → Connected apps** is the shared card. `/api/me` → `apps {chat, todo, panel}`: what is
    offered now (the other app said it can, within 24 hours) and the page path.
  - **The page path** (`panel`): `GET {SUPERVISOR_API}/addons/self/info` at start-up (`slug`, `ingress_panel`), else
    the `HOSTNAME` fallback accepted only as `^([0-9a-f]{8}|local)_household_docs$`; no sidebar page → no `panel`.
  - **The dialog**: ⋯ → Send to chat… (documents, folders, files; not for children) asks Chat for the person's chats
    (`chat.chats.list`, `requested_by` = the person, limit 50, expiring after **2 minutes**); the page polls
    `GET /api/bus/requests/{id}` with a spinner. Not installed / not said hello in a day → 409 at once ("Household
    Chat isn't available — …"); no answer within the expiry → "Household Chat didn't answer in time — is it
    running?" with *Try again*; a nack in plain words (no_access, not_found chat, read_only, invalid …). Each chat
    shows its name and member count. *(security review 2026-10)* The list names nobody (it goes through Home Assistant's event history):
    when the person ticks *Also give the chat's members access* with a chat picked, Docs asks again for **that one
    chat** (`chat.chats.list` with `chat_id`, a `members` request) and shows the Docs users in it who **can't open
    it yet**, each ticked (and how many aren't Docs users); the person unticks anyone who shouldn't get it.
  - **The card** (`chat.card`, 24-hour expiry): `title` (a document's name without its extension), `type` (note /
    checklist / sheet / folder / file), `item_id`, `owner_name` (left out in admin shared folders), `panel`, `target`
    (`/doc/<id>`, `/folder/<id>` or `/file/<id>`), `badge: "Docs"`, `share_with_members`. Anyone who can open the
    item may send it; **members' access** is offered (and accepted by the server) only to its **owner or a
    manager**, never in admin shared folders (no per-item shares) and not in read-only mode (423). The shares are
    made **only after Chat's ack**, by the sender, *(security review 2026-10)* and only for the people the sender **confirmed** in the
    dialog (`memberIds`) out of those Chat named for that chat in the person's own, finished `members` request for
    this item (409 otherwise) — an answer can narrow that list (the ack's `member_ids`: people who left meanwhile),
    never widen it, and an ack about another chat shares with nobody; checked again then (still owner or
    manager, the item still there): Docs users only, not the owner or the sender, not people who already have that
    much; refusals (a turned-off person) are listed; the new shares notify as usual (§11), after the answer is
    recorded. The page waits up to 20 s for the ack, then says "Chat posts it as soon as it answers".
  - **Open in Docs**: `/doc/<id>`, `/folder/<id>`, `/file/<id>` answer a relative redirect to `../#/…`; at start
    the page also reads Home Assistant's address (`parent.location`, same origin) and the
    `home-assistant/properties` message (`route.path`), opens the item (a file: its folder with the file panel) and
    replaces Home Assistant's address with the bare page path (`textutil.js` `appLinkHash`, Node-tested). `#quick-note`
    and `…/quick-note` work as before (§17.13). A 404 on a document, a folder or a file shows the 🔒 No access page above: "This document (folder, file —
    from the link, not the server) isn't shared with you. Ask the person who sent it."

### 17.16 Checklist → Todo list (app bus)
- ⋯ → **Make a Todo list** on a checklist, or select items → **Send to Todo**: Todo creates a list (or adds to one you
  pick) with those items as tasks; dates, assignees and reminders are then set in Todo. Offered only when Todo is
  installed and says it can (`todo.items.add`).
- Choice per send: *Keep in Docs too* (default) or *Move* (items leave the Docs checklist once Todo acks). Ticked items
  aren't sent unless you include them.
- Todo checks you may add to the target list. Docs keeps a note of where items went ("Sent to Todo › Weekend, 5 items").
- *(built, step 12)* One dialog (⋯ → Make a Todo list…) does both: **Where** — a new list (name, 1–60, the
  checklist's title first; shared with the household or just me) or a list I already have (asked from Todo,
  `todo.lists.list`, 2-minute expiry, a spinner); **Items** — every item with a tick box (the open ones ticked;
  ticked items greyed until *Include ticked items*); **Here in Docs** — Keep (default) or Move (editors only, not
  in read-only mode). Viewers may send (Keep). A sub-item whose own task isn't sent goes as a task (otherwise Todo
  would put it under the wrong one); item text is made one line and cut at 200 characters (Todo's limit); a task
  with more than 100 sub-items is refused before anything is sent. **Long checklists** are split into messages of
  at most 200 items and ~6.8 KB of data (the event stays under 8 KB), a task always with its sub-items; the first
  carries `new` (or the chosen list), each next one is sent only after the previous one's ack, with the `list_id`
  from the first answer (`todo_sends.parts`). **Move** takes a part's items out of the checklist (by their keys, as
  the sender, through the normal checklist operations) only after that part's ack; a nack or the local expiry
  stops the send, leaves the remaining items in Docs and says why ("Open Household Todo once first …") — parts
  Todo did take before that still leave the checklist (fixed 2026-10: when a later part failed before the earlier
  part's items were taken out, they used to stay, so they were in both apps). The texts
  are dropped from the database once sent or stopped. **The note** on the checklist (everyone who can open it):
  the last three sends — who, "Sent to Todo › Weekend, 5 items", moved or not, still sending or failed (the
  reason only for the sender); a personal list's name only for the person who sent to it.

### 17.17 Templates
- ➕ New → **From a template**: built-in templates for things Shopping and Todo don't cover — meeting notes, home
  maintenance log (a sheet: date, what, who, cost), monthly budget (a sheet with categories, totals and a "left"
  formula), emergency contacts, babysitter info sheet, trip plan (a note with sections). No grocery or packing lists.
- **Save as template** on any note, checklist or sheet (yours, or for the household — admins). Templates are files in
  a hidden `.templates` folder (per person and one for the household), so they're plain files too.
- *(built, step 12 — `templates.py`)* Built in (in the code): Meeting notes, Emergency contacts, Babysitter info and
  Trip plan are Markdown notes; Home maintenance log and Monthly budget are app-owned `.xlsx` sheets (the budget:
  ten categories, Planned / Spent / Left with `=B-C`, currency format, red negatives, a colour rule for overspent
  and a SUM totals row). Yours: `people/<you>/.templates/`; the household's: the documents folder's `.templates/`
  (admins save and remove them). Using one makes an ordinary document through the normal create (the folder you're
  in, quota, read-only mode, filing rules don't apply: it isn't an arrival); a checklist template stays a
  checklist. At most 100 templates per folder, 5 MB each; names checked like any file name; template refs are
  realpath-checked inside their folder. Children see no sheet templates.

### 17.18 Filing rules
- Per folder (owner/editor, or rw for admin shared folders): **when a file arrives** (uploaded, scanned, or added
  outside the app and found by the index) and matches a rule — name pattern (wildcards as in §10.2), type, size — then
  **rename** (a pattern with `{date}`, `{yyyy}`, `{mm}`, `{name}`, `{n}`) and/or **move** to a folder you can edit.
  Example: `Power-bill*.pdf` in Inbox → "Bills › Electric" as `{yyyy}-{mm} Electric.pdf`.
- Rules run in order, first match wins; every action shows in Activity (§17.4) with *Undo* for 7 days. A dry run
  ("Show what would happen") lists matches before a rule is saved.
- With AI on (§17.6): **Suggest name and folder** for a new scan from its text, as a suggestion to accept, never
  automatic.
- *(built, step 12 — `filing.py`)* Rules (`filing_rules`, at most 20 per folder) sit on a folder or an admin shared
  folder's top (`root:<id>`); *decision*: **not on the My docs top** (a rule there would act on everything that
  arrives in your space — make an Inbox). Types: notes, checklists, sheets, PDFs, pictures, other files; sizes in KB.
  Placeholders also `{dd}`; the extension always stays (added when the pattern has none); `{n}` is the first free
  number; a name taken by something else gets " (2)". Moves stay in the same space. **Arrivals**: a new upload or
  scan (`files.finish_upload`; also each file of a .zip / Keep import) and a new file the index finds (the
  "outside the app" Activity row) go to `filing_queue`; the queue is handled straight after an upload, after each
  index walk and every housekeeping tick — not while read-only mode is on (they wait). Documents made, copied or
  restored in the app are no arrivals. A rule **acts as the person who made it** (or last changed it), with their
  rights at that moment — if they can no longer change the file or the destination, nothing happens. Each action is
  one 🕑 Activity row `filed` (`detail {from, to, toFolder, log}`; the move / rename rows it made are folded into it)
  and a `filing_log` row; **Undo** (anyone who can change the file, for 7 days, once) moves it back where it
  arrived — the top of its space when that folder is gone — under its old name (" (2)" if taken). Follows tell
  "A filing rule filed …". The dry run tries the rule on the files already in the folder (≤ 100 listed) and changes
  nothing. Changing rules waits in read-only mode (like tags). The **AI suggestion** (`POST /api/ai/suggest-filing`,
  editors, AI on): sends the file's text (PDF text or text read by AI, ≤ 20 000 characters) and the names of the
  folders in the same space the person can change (≤ 150) → a name and a folder shown in the file panel with
  *Accept* (rename + move) or *No thanks*; usage purpose `filing`.

### 17.19 Clean-up rules
- Per folder: **delete after N days** (to Trash, so it's recoverable for `trash_days`) or **archive after N days**
  (move to an "Archive" subfolder), counted from last change. Example: "Quick notes in Inbox → Trash after 30 days".
- Shown on the folder ("Items here move to Trash 30 days after their last change"); a daily job; nothing in a folder
  without a rule is ever touched; favourites and pinned items are skipped.
- *(built, step 12)* One rule per folder (`cleanup_rules`; 1–3 650 days), set in the same dialog as the filing rules.
  The job runs from the hourly housekeeping, each rule at most once in 23 hours; it acts on the folder's own files
  and documents (*decision*: not on folders inside it, and not recursively), skipping anything **anyone** made a
  favourite or pinned; "Archive" is the subfolder of that name (any case), made when needed. It acts as the rule's
  maker through the normal delete / move rules (an editor can't trash a shared item); it **does nothing while
  read-only mode is on** (and the run isn't counted). The folder head and its row carry the sentence; Activity shows
  the deletions and moves as usual.

### 17.20 Kids' space
- An admin marks a person as a **child** (as in Household Arcade). A child sees only folders shared with them (and a
  **Kids** folder an admin can create and share), with a simple editor: notes and checklists, no sheets, no
  sharing, no Shared folders, no AI, no regex search.
- Their own My docs is still theirs; parents can be given view access to it (admin setting per child).
- *(built, step 12 — `kids.py`)* Admin → People has a **Child** switch per person (beside Access) and, for a child,
  *Let parents view…* (the people who get **Can view** on everything in the child's My docs, `kid_parents`; turning
  the child off removes them). The **Kids folder** is an admin shared folder ticked *Kids folder* (`roots.kids`).
  Enforced on the server: in the access CTE and `role_of` / `root_role` a child gets **no Everyone shares** and
  only Kids folders among the shared folders (*decision*: "only folders shared with them" — the household's
  Everyone shares can be anything); `/api/space/everyone` is empty for them. Routes in `kids.BLOCKED` answer 403
  ("This isn't available in Kids' space."): sharing (Share…, Transfer), sheet export / import / Save a copy to edit,
  Send to chat, and every `/api/ai` route. Checked by content: no sheets (make, open, save, convert, restore, sheet
  templates, Save as template), no regular expressions in search, `canShare` false everywhere. Allowed: notes,
  checklists, uploads, scans, imports, tags, pins, Quick note, follows, Make a Todo list. A test walks the app's
  route table: every route must be blocked, content-checked or allowed in `kids.py`. The page leaves out ➕ New →
  Sheet / Import a sheet, Everyone, Share…, Transfer, Send to chat, AI and the regex options. Parents find each
  child under 🧒 **Kids' docs** (`#/kids`, sidebar sub-links; the child's top is `root:<child's root>`, read only).
- *(security review 2026-10)* **No content access for admins through Kids' space**: an admin can't name themselves as a parent (403;
  another admin has to). **Accepted by design** (re-review 2026-10): any admin may mark any person as a child and
  name **any other** person — another admin included — as a parent, who then reads what the person makes from then
  on. That power is an admin's, stated plainly in DOCS.md (Kids' space; Who can see what); it is never silent — the
  person is notified (below) and every change is audited. Marking a person as a child records `users.child_since`; a parent's view covers only
  items **made through the app since then** (`nodes.created_by` set and `nodes.ctime ≥ child_since`, in the access
  CTE and `role_of`) — what the person had before, and files added outside the app, stay theirs alone (*decision*:
  the safest rule that keeps the feature useful for a real child, whose documents are made after they're marked).
  Listings, the Move/Copy picker, crumbs, Make a copy and `.zip` downloads in a child's My docs leave out what a
  parent can't open (a `.zip` is built from the index there). Turning *Child* off clears `child_since` and the
  parents. The person is **told**: a notification (whatever their share-notification setting) when they're marked
  as a child, when that's turned off, and when the parents change — "An admin marked you as a child in Household
  Docs. Meera can view what you make in My docs from now on (not what you had before)." — and `/api/me` →
  `kidsStatus {isChild, parents: [names], since}`, shown at the top of their My docs. `child_on` / `child_off` /
  `child_parents_set` are audited with the child's root. Make a copy is content-checked for children: no sheet, nor
  a folder holding one (403).

### 17.21 What changed since you last looked
- Each person's last-opened time per document (`user_state.opened_at`) is compared with its changes: a dot on items
  changed by someone else since you last opened them, in lists and search; *Mark all as seen* per folder.
- Opening a changed note highlights the changed lines (diff against the version you last saw, §7.3) for that visit;
  changed checklist items and sheet cells get a soft highlight. A "Changed since you looked" filter in search
  (`is:new`).
- *(built, step 12 — `changes.py`)* *Decision*: a separate **last look** (`user_state.seen_at` + `seen_sha`, the content
  seen), so Mark all as seen doesn't fill Recent. A look is recorded on opening a document (and a folder), on your own
  saves (your changes are never new to you) and by Mark all as seen. An item has the dot when it isn't a folder,
  someone else — or something outside the app — changed it last, its file is newer than your last look, and its
  content isn't what you saw; items you never opened count from `users.seen_from` (when this started for you), so
  the past isn't all "new". The dot shows in folder lists, spaces and search results; `is:new` (also `is:changed`)
  and the ● option in the Search page's *Is* filter; **Mark all as seen** in the folder head when something there
  has a dot (the folder and everything inside it you can open). **Opening** a changed document you had looked at
  before returns `changedSince {since, by}` and, when History has the version you saw (matched by SHA-256): a note's
  old text (the page shows "Changed by Alex since you last looked" with *Show what changed*: the line compare,
  removed and added lines highlighted), a checklist's new or changed items (ticked / unticked too; highlighted),
  a sheet's changed cells per tab (≤ 2 000; outlined). Without that version only the note and who. The first look
  at something shows nothing to compare (its dot said "new").

### 17.22 Decided against (2026-10-04)
Receipt → Docs filing (receipts and manuals stay separate; manuals and bills are just files, §1.1), linking Family Tree
people to Docs folders (the two apps stay separate), dated logs,
appending from automations / voice, a wall-tablet view, a visitor sheet, comments with @mentions, and expiry dates
with reminders on documents (Household Vault already does expiry reminders for what matters).

### 17.23 Answering the Household Assistant (`tools.py`; HOUSEHOLD_ASSISTANT_SPEC.md §4.2)
- **Over the bus**: `app_messages.start()` installs `tools.tools` (the shared `assist_tools.py`, APP_MESSAGES_SPEC
  §6.6) and says `can: ["assist.tool.call", "assist.tools.list"]` in its hello — the only kinds Docs answers.
  `requested_by` is the actor (an enabled `users` row as `auth.user_dict`, never with an admin's powers); `nack busy`
  while a backup is restored (`db.RESTORING`). Links: the sidebar page (`app_messages.panel()`, §6.5 there) and
  `/doc/<id>`, `/folder/<id>` or `/file/<id>`.
- **Tools** (all through `sharing.require` / the search's own access check: what the person could open here; in
  Trash, or not theirs to see → `nack not_found document`, the app's 404):
  - `docs.search` (`query`, `kind?` note|markdown|checklist|sheet|folder|pdf|image|spreadsheet|document): the Search
    page's `engine.run` + `describe`, first 10 results as `{id, name, type, folder, snippet, modified}`.
  - `docs.read` (`id`, `offset?`): 4000 characters of a note's or checklist's text from `offset` (`more`,
    `next_offset`), or a sheet's cells as `"Tab A1: value"` lines (`sheet_model.index_text`); read-only — no
    `touch_opened`, no "changed since" mark, no index refresh. A child gets no sheets (Kids' space).
  - `docs.checklist` (`id`): `{item, done, level}` per item.
  - `docs.note.create` (`acts`; `name`, `text` ≤ 4000): only with `confirm: true`; `docops.create` into My docs →
    Inbox (the quick note's folder, `pins._inbox`), links updated; not for children.
- **Switches**: App settings → **Answer the Household Assistant** (`assistant_answers`, default on) and Settings → You
  → **Let the Household Assistant answer for me** (`prefs.assistantOk`, default on). Otherwise `nack not_allowed`
  (`off` / `person_off`); a turned-off or unknown person is `no_access`.
- **Privacy**: results carry document text, so DOCS.md makes the recorder exclusion a must for anyone using the
  assistant (APP_MESSAGES_SPEC §8).

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
- **Backups without secrets**: the download blanks the secret App settings (`ai_api_key`, in the zip's docs.db) in the copy (`settings.REGISTRY.scrub_secrets`); a restore keeps this install's value for each one the file leaves blank (`saved_secrets` before, `keep_secrets` after the migrations; a value the file carries is used).
