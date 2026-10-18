# One person's data: export, import, delete — spec

Status: **spec only (2026-10-18), nothing built.** For Finance Dashboard, Calorie Tracker, Household Arcade,
Household Docs and Household Assistant. Each app ships its part as its own update (§10).

## 1. What it is

An admin can, for **one person** at a time:

- **Export** everything that person has in the app as one file, to keep, to move them to another install, or to give
  them a copy.
- **Import** such a file back — into the same person or another one, on this install or another.
- **Delete** everything that person has in the app, for good.

Everything sits under **Admin**, on each person's card in the app's people list (§3). Nobody else can see or use it;
the server checks it on every request (admin only, never "acting as").

Not in scope: changing the whole-app backup and restore (they stay as they are), deleting people from Home Assistant,
and the other apps (Splitpot, Todo, Chat, Vault, Family Tree, Proxy, Receipt Price Intelligence, Household AI).

## 2. Rules for every app

### 2.1 What "one person's data" means

- **Theirs:** rows and files that belong to them alone — their food log, their bank statements, their scores, the
  documents in their own folder.
- **Shared, and kept:** things other people also depend on — a two-player match, a shared folder, a household setting.
  Export takes only the person's own part (or a read-only record of it). Delete leaves the shared thing working for
  everyone else: the person is shown as **Former member** (Docs, Finance) or **Former player** (Arcade) where
  their name used to be.
- **Never in an export, never touched by an import or a delete:** App settings, secrets (AI keys, the app-messages
  signing key), other people's data, the household's history totals (AI usage running totals), and the shared
  app-message tables.

### 2.2 The file

One `.zip`, named `<app>-<person>-<YYYYMMDD-HHMM>.zip` (e.g. `calorie-tracker-alice-20261018-1235.zip`):

```
manifest.json          what this is (below)
data/<table>.json      the person's rows, one JSON array per table, as the app stores them (columns by name)
files/…                the person's files, where the app has any (Finance PDFs, Docs documents)
README.txt             one paragraph: what the file is, which app reads it, that it holds personal data
```

`manifest.json`:

```json
{
  "format": "household-person-data", "v": 1,
  "app": "calorie_tracker", "appVersion": "2.5.0", "schema": 12,
  "exportedAt": "2026-10-18T18:35:02Z", "exportedBy": "adminy",
  "person": { "id": "8f2c…", "username": "alice", "name": "Alice" },
  "counts": { "food_logs": 812, "symptom_logs": 40, "files": 0 },
  "options": { "pdfs": true },
  "sha256": { "data/food_logs.json": "…", "files/…": "…" }
}
```

- `schema` is the app's own data version (Finance: `schema_version`; Arcade: its migration number; Calorie and Docs
  start one with this feature).
- The file is **not encrypted** — it holds personal data (health, money, documents). The export button says so, and
  the download is offered only, never stored by the app. Option, later: a password (zip AES) — not in v1.

### 2.3 Export

- Admin → the person's card → **⋯ Data → Export their data**. A dialog shows what's in it (the counts, §4–8 per app),
  the app's options (e.g. Finance "Include statement PDFs", on), and **Download**.
- Small exports download at once; big ones (Docs, Finance with PDFs) run as a background job with progress and a
  **Download** link when done (kept 1 hour, then deleted — the backup files' sweep).
- An entry in the app's log (and its audit or activity log where it has one): who exported whom, when, the counts —
  never the content.

### 2.4 Import

1. Admin → the people list → **Import a person's data…**, or on a card **⋯ Data → Import into this person**.
2. Choose the `.zip`. The app checks it (§9) and shows a **preview**: from which app and version, whose data, when it
   was exported, the counts, and any warnings ("made by a newer version — can't be read", "3 saved foods have no
   calories", "PDFs left out").
3. Pick **who it goes to**: the person it came from if they're here, else anyone in the list (defaults to the card's
   person).
4. Pick **how**:
   - **Add to what they have** (default when they already have data): rows are added; ones already there (§4–8 say
     how a duplicate is recognised per app) are skipped and counted.
   - **Replace what they have**: their current data is deleted first (as §2.5, without forgetting them), then the file
     is loaded. Asks for their name to be typed.
5. **Import**. A result line: "Imported 812 food entries, 40 symptoms; 3 already there were skipped."

- Every row gets a **new id**; references inside the file (a food entry's saved food, a statement's account) are
  mapped to the new ids. Ids in the file never collide with anyone's.
- The file's `person` is information only: it decides nothing about who may do what.
- One import or delete at a time per app; others wait or get "busy, try again in a minute".
- Same version or older files only. An older file is brought up to date by the app's own upgrade steps for each
  table (the same ones its database migrations make); a newer one is refused.

### 2.5 Delete

Admin → the person's card → **⋯ Data → Delete their data…**:

- The dialog lists what will go (the counts, per kind, from a dry run), and what stays for others (shared things,
  §4–8), then two choices:
  - **Delete their data, keep them** (default): the person stays in the list, empty. Use it to start them afresh.
  - **Delete their data and forget them**: their row goes too. If they still have a Home Assistant login they come
    back as a new, empty person the next time they open the app (Docs adds every Home Assistant person every 5
    minutes) — the dialog says so, and suggests removing them in Home Assistant first if they shouldn't come back.
- **Download a copy first** (ticked by default) runs §2.3 before deleting.
- Confirm by typing their name. **Delete** is red and says it can't be undone.
- It removes their data in one database transaction (files after the commit, so a failed delete leaves the database
  untouched and only a few files to tidy), removes their Home Assistant sensors (§4–8), and writes one log entry.
- What it can't reach, said in the dialog: **whole-app backups and Home Assistant backups made before** still hold
  their data, and so do Finance's upgrade copies (`finance.db.pre-NNNN`).

### 2.6 Shared building blocks (`common/`)

- `common/python/person_data.py`: the zip writer and reader (manifest, checksums, `README.txt`), the checks of §9,
  the id remapping helper (`IdMap`), the job runner for big exports (using `backup_core`), and the name-confirmation
  check. Each app gives it a list of its tables: name, person column, how to read the rows, how to write them, how a
  duplicate is recognised, which columns are ids to remap.
- `common/static/person-data.js` (+ `person-data.css`), `window.PeopleData`: the export, import and delete dialogs.
  `PeopleData.menu(person, cfg)` is the **⋯ Data** button an app puts on a person's card or row (`PeoplePage`'s
  `actions`, or a table cell); `PeopleData.importButton(cfg)` the **Import a person's data…** button above the list;
  `PeopleData.open(person, "export" | "import" | "delete", cfg)` opens one directly. `cfg = {people: [{id, name}]`
  (the import targets)`, onDone()}`. It needs only `common/ui.js` and the shared theme colours.
- The summary the dialogs show (`GET …/data`): `{person: {id, name}, counts: [{key, label, count, detail?}],
  exportOptions: [{key, label, default, help?}], exportNote?, keeps: [text], cannotReach: [text],
  deleteChoices: [{key, label, help?, default, options: [{value, label, help?}]}], forgetNote?, deleteNote?}`.
  The import preview: `{app, appVersion, exportedAt, exportedBy, person: {id, name}, counts, warnings: [text],
  options: [{key, label, default, help?}], target: id | null}`. Delete takes `{confirm, forget, choices: {key: value}}`;
  import `{mode, confirm, options: {key: bool}}`; export `{options: {key: bool}}`, answering with the .zip, or
  `{job}` for a big one (then `GET …/data/jobs/{job}` and `…/jobs/{job}/file`). Errors: 400/422 with a sentence in
  `detail`, 409 for a wrong confirmation, 423 when another import or delete is running.
- Shared API shape (each app under its own Admin prefix):

| Method | Path | What |
|---|---|---|
| GET | `/api/admin/people/{id}/data` | the counts per kind, for the dialogs (also the delete dry run) |
| POST | `/api/admin/people/{id}/data/export` | `{options}` → the zip, or `{job}` for a big one |
| GET | `/api/admin/people/data/jobs/{job}` | progress: `{state, done, total, filename, error?}` |
| GET | `/api/admin/people/data/jobs/{job}/file` | the finished .zip (kept 1 hour) |
| POST | `/api/admin/people/data/import` | multipart `file` → `{token, preview}` (the file is kept 1 hour) |
| POST | `/api/admin/people/{id}/data/import/{token}` | `{mode: "add" \| "replace", confirm?}` → the result |
| POST | `/api/admin/people/{id}/data/delete` | `{confirm: "<name>", forget: bool}` → what was deleted |

## 3. Where it goes in each app's Admin

| App | Admin tab | The person's card |
|---|---|---|
| Calorie Tracker | **Admin → Users** (shared people list) | ⋯ Data on each card; **Import a person's data…** above the list |
| Household Arcade | **Admin → Users** | the same |
| Household Docs | **Admin → People** | the same; the existing **Delete their documents** moves into ⋯ Data as one of the choices (§7.4) |
| Household Assistant | **Admin → People** (its own table) | a **Data** button per row opening the shared dialogs (`PeopleData.open`) |
| Finance Dashboard | **Admin → Users** (`admin-storage?tab=users`, server-rendered) | a **Data** button per row opening the same three dialogs (Finance's pages are templates, not `people.js`); the existing **Wipe all data** (`/user-wipe`) stays as the soft, undoable wipe, and links to the new Delete for "for good" |

## 4. Calorie Tracker

**Who:** `users.id` (the Home Assistant user id). Today nobody is ever deleted; every per-person table cascades from
`users` (`ON DELETE CASCADE`), except `ai_usage`.

**Export:** `users` (name, enabled), `goals`, `weight_logs`, `saved_foods`, `food_logs` (with `saved_food_id` mapped,
`no_calories`, `eaten_at`, `tags`), `symptom_logs`. Not exported: `ai_usage` (a counter for the limits), the
household's symptom and tag lists (App settings). The dialog notes that symptoms and weight are health data.

**Import — a duplicate is:** a food entry with the same date, time, meal, food and servings; a symptom with the same
date, time and symptom; a weight on the same date; a saved food with the same name (folded). Goals: **Add** keeps
the person's goals, **Replace** takes the file's.

**Delete:** `DELETE FROM users WHERE id = ?` (cascades) and `DELETE FROM ai_usage WHERE user_id = ?`; **keep them**
puts back an empty `users` and `goals` row. Their `sensor.calorie_tracker_<slug>_daily_calories` is removed — a new
`ha_sync.remove_for_user` (there is none since 2.4.2). The Household Assistant then finds nobody (it already answers
"not found").

**Counts shown:** food entries, symptoms, weights, saved foods, days logged, first and last day.

## 5. Finance Dashboard

**Who:** `known_users.id`. Every data table has a plain `user_id` (no foreign key to `known_users`). Files are in
`/data/pending/<user id>/` (statement PDFs, `utility-bills/`, `toll-statements/`).

**Export:** `accounts`, `statements`, `transactions`, `csv_imports`, `skipped_duplicates`, `category_rules`,
`custom_categories`, `recurring_overrides`, `utility_bills` (+ `electric_meter_details`), `toll_statements`,
`toll_transactions`, `toll_cars`, `toll_devices`, `toll_patterns`, `toll_pattern_groups`, `toll_trip_overrides`
(`toll_trips` is a cache: rebuilt on import, not exported), and their PDFs under `files/`.

- Options: **Include statement PDFs** (on); **Include Recently deleted items** (off — rows with `deleted_at`); **Include
  the text read from statements** (on: `llm_raw_response`, `raw_line`, … — needed to re-check a statement later).
- Rows other people added while viewing this person's data (shared access) are stored under this person's id, so they
  are this person's data and are exported (Finance records no other author).
- Not exported: `user_access` grants (who may see whose data is a household decision, made again after an import),
  `saved_reports` (app-wide), `ai_usage_log` (the household total), App settings.

**Import — a duplicate is:** an account with the same name and last 4 digits; a statement with the same PDF hash (the
existing duplicate check); a transaction the app's own duplicate detection already finds (§6 of its spec); rules,
categories and toll setup with the same name or pattern. After loading: `rebuild_trips`, transfer pairing for the
person, and the PDFs are moved into `/data/pending/<new owner>/` with `pdf_path` rewritten. Refused while an upload is
being processed for that person (as Wipe all data is).

**Delete** (for good, unlike Wipe all data, which only marks rows deleted and leaves rules, categories, tolls and the
person behind):

- Hard-delete every table above for the person, in the order that respects the foreign keys
  (`toll_transactions` before `toll_devices` — that key has no `ON DELETE`), `toll_trips`, both sides of
  `user_access` (as viewer and as owner), and `known_users` with **forget them**.
- Delete their `/data/pending/<user id>/` folder after the commit (a dual-fuel bill's shared PDF is the same person's).
- `app_settings.updated_by` and `user_access.granted_by` naming them are left (admin history).
- No Home Assistant sensors to remove. The Household Assistant then finds nobody.

**Counts shown:** accounts, statements (and PDFs, with their size), transactions, utility bills, toll statements, rules
and categories, shared-access grants (both ways).

## 6. Household Arcade

**Who:** `users.id`. Per-person settings are columns on `users` (look, sound, handedness, reduce motion, notifications,
child, game preferences, favourites). Today nobody is ever deleted (there is no delete path at all).

**Export:** `users` (their settings), `scores`, `saved_games`, `level_progress`, `play_sessions` (their play history,
as long as housekeeping keeps it), `child_limits` and `extra_time` (when they're a child), `user_notify`, and a
**read-only record of the matches they played** (`matches.json`: game, mode, when, the other players' names, their
result, the winner) — matches need the other players, so they're kept for reading, not loaded on import. Not
exported: `notify_log`, `ai_calls`, levels (household-wide), holidays.

**Import — a duplicate is:** a score with the same game, mode, score and time; a saved game for the same game (Add
keeps the person's, Replace takes the file's); level progress for the same game and mode (the further one wins). Child
limits are imported only when the admin ticks **Also their play-time limits** (off by default — limits are set for a
child in this house). Imported scores take part in records and the leaderboard like any other (the preview says so).

**Delete:**

- Delete `level_progress` first (its key to `users` has no cascade), then the person's rows: `scores`, `saved_games`,
  `play_sessions`, `child_limits`, `extra_time`, `user_notify`, `notify_log`.
- **Matches**, so the others lose nothing:
  - a match still going: their seat goes to the computer, as when a player leaves (`match_players.computer`);
  - a match they created that has other people in it: `matches.created_by` moves to the next person in it (that key
    cascades — deleting the creator would delete the match for everyone);
  - a match with nobody else left: deleted;
  - `matches.winner` and finished seats keep their id; pages show **Former player** for an id with no person.
- With **forget them**, their `users` row goes (cascades whatever is left).
- `levels.approved_by`, `level_builds.requested_by` and the "who did it" text columns keep the name they had (they're
  admin history, not the person's data).
- Home Assistant: their `sensor.household_arcade_<slug>_played_today` and `binary_sensor.…_playing` are removed; the
  game record sensors are worked out again (a record that was theirs passes to the next best).

**Counts shown:** scores (and records held), saved games, games with level progress, matches played (and still
going), play history days, child limits.

## 7. Household Docs

**Who:** `users.id`; their own folder is the person root (`roots.kind = 'person'`) at
`<docs folder>/people/<users.folder>/`. Today the person row is never deleted; **Delete their documents** only moves
their top-level items to their Trash.

### 7.1 What is theirs

- **Their folder:** every node in their person root, its versions (`.versions/<node-id>/`), tags, ticks, links, pins,
  OCR and PDF text; their templates (`people/<folder>/.templates/`); their Trash (`.trash/<folder>/`).
- **Their own things elsewhere:** prefs, `user_state` (favourites, pins, last opened, seen), saved and recent searches,
  follows, `user_notify`, Google Keep import records, filing and clean-up rules on their own folder.
- **Not theirs, though they touched it:** documents they created or edited in someone else's folder or an admin shared
  folder (those belong to the folder's owner — Docs' ownership rule), versions they saved of those, tags and ticks
  they added there, rules or sensors they set on shared folders.

### 7.2 Export

- `files/` = their folder as it is on disk (what **Download → My docs** gives today, plus the hidden templates), and
  with **Include old versions** (on) `files/.versions/…`, with **Include Trash** (off) `files/.trash/…`.
- `data/` = the nodes of their folder (path, type, tags, colours, links between their own documents, pins,
  favourites, ticks, OCR corrections), the shares they gave on their own items (who, how — information, see import),
  saved searches, follows of their own items, prefs, rules on their folder, Keep import records.
- A list `elsewhere.json` of what they created in other folders (path and folder, no content) — so the admin knows
  what stays behind.
- Big: runs as a job (§2.3), with the whole-app backup's limits (10,000 files, 4 GB; above that the dialog says to use
  the whole-app backup with files).

### 7.3 Import

- **Add** puts everything in a new folder **Imported <date>** in the person's own folder (nothing they have is
  touched); **Replace** moves their current folder's contents to their Trash first, then unpacks at the top.
- Files are checked like uploads (size, quota, type, names) and written with the app's own file writer, then indexed
  (search, PDF text) as a scan would.
- Tags, links, pins, ticks and versions come back with their documents. Shares come back only with **Also share with
  the same people again** (off), and only with people who are here.
- A duplicate is a document at the same path with the same contents (sha256) — skipped.

### 7.4 Delete

The current **Delete their documents** becomes a choice in the same dialog, so there is one place:

- **What happens to their folder:**
  - **Move to Trash** (as today: undoable for `trash_days`; the person and their folder stay);
  - **Give to someone** (their folder's contents move into another person's folder as **From <name>**, with versions —
    Transfer ownership, for everything at once);
  - **Delete for good** (files, versions and Trash gone).
- **Then, with either of the last two:** their prefs, `user_state`, searches, follows, `user_notify`, Keep records,
  `ai_person_month`, `bus_requests`, `todo_sends`, rules on their folder, the shares **to** them, their
  `root_access` rows and Kids' space links (`kid_parents`, as child or as parent) are deleted.
- **Stays for others:** what they made in other folders (shown as made by **Former member**), versions they saved
  there, shares they gave on others' items, rules and sensors on shared folders (the admin is told how many), the
  audit log (it records the delete).
- **Forget them:** their `users` row and their person root go, and the folder name is free again. The dialog warns
  that Docs adds every Home Assistant person with a login every 5 minutes, so a person still in Home Assistant comes
  back, empty.
- Home Assistant: Docs' sensors are per item; sensors on their deleted items go with them (as when an item is
  deleted).

**Counts shown:** files and size in their folder (and versions, Trash), shares to and from them, things they made
in other folders, Kids' space links, rules.

## 8. Household Assistant

**Who:** `users.id` (the Home Assistant user id), with `enabled`, `is_child` and `reviewed` (an admin's). Today nobody
is deleted; there is no "acting as" — everyone's questions are their own, and **admins never see anyone else's**.

### 8.1 What is theirs

- Their questions and answers (`questions`, kept `keep_days`, 30 by default) with the tool calls behind each answer
  (`calls`, cascading — including what other apps answered about them), their briefing settings (`briefings`: morning
  and evening, times, days, speaker), their rate-limit marks (`rate_events`, a day's worth).
- Not theirs: learned quick phrases (`quick_phrases`) and unplaced words (`quick_misses`) — they store no person and
  help everyone; the household's usage totals (`usage_days`); the tool catalogue.

### 8.2 Export

- `data/`: `users` (name; `enabled`, `is_child` as information), `briefings`, and — only with **Include their
  questions and answers** (off by default) — `questions` and `calls`.
- That option is off because it is the one way an admin could read someone's questions; the dialog says so, and the
  export's log entry records whether it was on. Leave it off to move someone's settings; turn it on to give a person
  their own history.
- Not exported: `rate_events`, quick phrases, misses, words, usage totals, App settings.

### 8.3 Import

- Briefings: **Add** keeps the person's, **Replace** takes the file's; a speaker that this Home Assistant doesn't have
  is kept but shown as "not found" (as the page does today).
- Questions (when in the file): a duplicate is one asked at the same time with the same text. They count toward
  nothing (no rate limit, no usage totals) and are still removed after `keep_days` — the preview says how many are
  already older than that and won't be kept.
- The admin's flags (`enabled`, `is_child`, `reviewed`) are never imported: this install's admin decides.

### 8.4 Delete

- Stops any question of theirs that is still running, then deletes their `questions` (with `calls`), `briefings` and
  `rate_events`, and empties their answer-reuse cache (`engine.forget_recent`).
- **Keep them:** their row stays (same flags). **Forget them:** their row goes; the next time they ask they are new
  and waiting for an admin's review, as anyone new.
- What it can't reach, said in the dialog: app messages already sent to other apps (gone within 7 days) and
  Home Assistant's own event history (unless the recorder excludes `household_apps`, App settings tells the admin).
- Learned quick phrases stay (they store no person). No Home Assistant sensors.

**Counts shown:** questions kept (and the oldest), briefings on, the speaker.

## 9. Checks (import), and safety

- Admin only, server side; the apps' cross-site guard applies; never while "acting as".
- The upload: at most the app's backup upload size; a real zip; member names safe (`backup_core.check_members`: no
  `..`, no absolute paths, no links), total unpacked size and file count capped (zip bombs), every checksum in the
  manifest matches, `format` and `app` are this app's, `schema` not newer.
- Every row is checked with the app's own rules (the same Pydantic models and limits as the API — a too-long name or
  an amount out of range is refused with the table and row number), unknown columns are dropped, ids remapped.
- Nothing from the file is ever run or trusted as code or HTML (shown escaped); file contents go through the same
  checks as an upload (Docs' types, Finance's PDF sniffing).
- The import runs in one database transaction; files are written to a temporary folder and moved in after the commit;
  a failure leaves nothing half-imported.
- The uploaded file and finished exports are deleted after an hour (or at once after an import).

## 10. Building it (one update per app)

1. `common/`: `person_data.py`, the people-list **⋯ Data** menu and dialogs (`people.js`), tests (zip safety,
   checksums, id remapping, the dialogs' rules). No app changes yet.
2. **Calorie Tracker** (smallest: cascades already there) — export, import, delete, its sensor removal; tests.
3. **Household Arcade** — the matches rules, Former player, sensors; tests including a two-person match after a
   delete.
4. **Finance Dashboard** — the server-rendered Data dialogs, PDFs, the delete order, `rebuild_trips`; tests including
   a toll statement with devices and a shared-access grant.
5. **Household Docs** — files, jobs, the three folder choices, Former member; tests including a person with documents
   in a shared folder and a Kids' space link.
6. **Household Assistant** — small (no files, no shared rows); its People table gets the Data button; tests including
   a running question when the delete comes, and the questions option off.

Each app: its own `spec/SPEC.md` section, DOCS "Admin → Users / People → Data" section, CHANGELOG and version bump,
App settings untouched. Tests for every app: export → delete → import gives back the same counts; import into another
person; a newer-version file refused; a tampered file refused; others' data unchanged after a delete.

## 11. Decided here (can be changed)

- One zip format for all five apps, JSON inside (readable, easy to check), not a SQLite file.
- New ids on every import (no collisions, imports into anyone).
- **Add** is the default import mode; **Replace** asks for the name.
- **Keep them** is the default delete; **forget them** is a choice.
- Docs' delete offers Move to Trash / Give to someone / Delete for good; **Move to Trash** stays the default.
- Not exported: grants and access (Finance shared access, Docs root access), App settings, secrets, AI usage.
- The Assistant's questions are exported only when the admin ticks it (off by default).
- Matches (Arcade) are exported as a record only.
- No encryption in v1.
