# Family Tree — design spec

A shared family tree for the household, as a Home Assistant app. This document describes the current behaviour. The app follows the household app conventions in `HA_ADDON_PATTERNS.md` §4 (ingress-only auth, look and feel, admin and backup conventions); the notification plumbing (`ha_notify.py`, `ha_people.py`) is shared, byte-identical, with the other household apps.

## 1. Purpose & scope
- **One tree per app,** shared by the household. **Every enabled HA user can view and edit.** Every change is recorded with who and when, and can be undone.
- **People** have rich records:
  - names, including birth/maiden and other names;
  - gender;
  - birth and death dates, times and places, with approximate dates allowed;
  - photos, life events, a biography and stories, and attached documents.
- **Relationships** use GEDCOM-style *families*: two partners (or one) plus their children. A child's link records whether they are a birth, adopted, step or foster child.
- **Tools:**
  - **"This is me":** each HA user can be linked to their person in the tree, so it opens centred on them and shows "your cousin once removed".
  - **Reminders:** opt-in birthday, anniversary and remembrance notifications through HA (§9).
  - **Export:** a standalone family website (§13.6), a wall chart and a family book (§13.5).
  - **Optional modules** (§3.1), each behind a feature switch: photo tagging, photo fixes, the inbox folder, custom fields, sources, contacts, duplicates, the quiz, printing, the website export, milestones, father's/mother's side, the places map, Indian relationship names, names in Telugu/Hindi script, tithi dates and Indian ceremonies.
- **Out of scope:**
  - HA sensors;
  - DNA data;
  - automatic face detection;
  - multiple separate trees;
  - review/approval of edits;
  - sharing outside the household.

## 2. Stack & file layout
- **Backend:** FastAPI + uvicorn on `python:3.12-alpine` (+ `tzdata`), port **8102**. Deps are pinned like the sibling apps, plus **Pillow** for thumbnails and image re-encoding. Raw `sqlite3` stores the data; photos and documents live on HA's **`/share`** folder (§5.1).
- **Storage decision: SQLite only** (Python's built-in `sqlite3`, WAL mode, `/data/family.db`), plus media files. There's **no separate database server**: it isn't needed at household scale, everything under `/data` is included in HA backups, and it matches the sibling apps. Media is kept **outside `/data`** so those backups stay small (§5.1).
- **Frontend:** plain HTML/CSS/JS with no build step and no CDN. The tree is drawn as **SVG built with DOM calls**, with its own layout code and pointer/wheel pan-zoom, so no chart library is needed. Leaflet (map) and Sanscript (transliteration) are vendored.
```
family_tree/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore  README.md  DOCS.md  CHANGELOG.md
├── icon.png  logo.png  translations/en.yaml   spec/SPEC.md   tests/
└── app/  main.py config.py auth.py db.py settings.py features.py dates.py names.py graph.py relations.py
          kin.py sides.py related.py history.py media.py photoedit.py inbox.py geocode.py duplicates.py
          upcoming.py reminders.py milestones.py panchang.py tithi.py ceremonies.py kidmode.py
          export_view.py site_export.py housekeeping.py common.py ha_client.py ha_notify.py ha_people.py
          routers/ me people families events stories tree history media export reminders kin map related
                   custom sources contacts duplicates tithi quiz admin
          static/ index.html app.js tree.js print.js backnav.js theme-boot.js style.css vendor/{leaflet,sanscript}
          site_assets/ site.css site.js
```

## 3. Manifest, options & App settings
- **Manifest:** `slug: family_tree`, `url: https://github.com/sameerkotra/ha-apps`, `ingress: true`, `ingress_port: 8102`, **no `ports:`**, `panel_icon: mdi:family-tree`, `panel_admin: false`, arch amd64/aarch64/armv7.
- **Folders:** `map: [{type: share, read_only: false}]`, for media (§5.1).
- **Permissions:** `homeassistant_api: true`. It reads Persons (the user list and their phones), HA's time zone and home location, lists notify services and calls notify for reminders. Nothing else: no sensors, `hassio_api`/`auth_api`/`docker_api`/`full_access` false, `apparmor: true`.

| Option | Default | Meaning |
|---|---|---|
| `admin_users` | `[]` | HA user ids or login names (never display names). Admins open **Admin** (App settings, Users, Storage, Trash): they disable users, link/unlink anyone's "This is me", restore from and empty the trash, change App settings and feature switches, and run backup and restore. Stays an app option (the app's Configuration tab): it's how the first admin is known. |

- **Every other setting is an App setting**, never an option in the app's Configuration tab. `config.yaml` keeps only `admin_users` (the bootstrap: someone must be an admin before App settings can be opened). Per-person choices go in that user's own Settings. Values other than `admin_users` in `/data/options.json` are never read.
- **First run — "No admin yet".** While `admin_users` is empty nobody is admin. Every page shows a banner to everyone: "No admin yet — add your Home Assistant user name (**<their user name>**) to `admin_users` in the app's Configuration tab, save, and restart the app", linking to the "How the app sees you" page. `/me` and `/whoami` report `noAdmin` (and `/whoami` `adminOption: "admin_users"`). Nobody is ever promoted automatically.
- **Reminders need no app options:** phones come from Home Assistant (Settings → People → Track device) plus any extra notify services an admin assigns in Admin → Users (§9), and each user picks their own digest time (default 08:00).
- **App settings** (Admin → App settings, admins only). Stored in the `app_settings` table (§5) as JSON, validated (Pydantic, strict types, unknown keys → 422), read through a small cache at the moment they're used, so a change applies **without a restart**.

  | Setting | Default | Meaning |
  |---|---|---|
  | `trash_days` | `90` | How long deleted people, families and media stay restorable (7–3650). Read by the daily purge and the Trash page. |
  | `max_upload_mb` | `20` | Per file (1–100). Read by every upload and by the request-size guard. |
  | `media_path` | `/share/family_tree` | The photo folder (§5.1): an absolute, normalised path inside `/share/` (not `/share` itself; no `.`/`..`, no trailing slash; the `/share` rule is lifted only by `ALLOW_ANY_MEDIA_PATH` for tests/dev, whose `MEDIA_PATH` env var changes the default). Changing it never moves files; see §5.1 "Changing the folder". |
  | `name_order` | `given_first` | `given_first` or `surname_first` ("Sharma Asha"). Any person can override it (§13.7). |
  | `relationship_language` | `en` | `en`, `te` or `hi` — the household language for relationship names (§13.1); each user can override it. Choosing `te`/`hi` needs the Indian relationship names switch (422 otherwise); while that switch is off the stored value is kept but English is used. |
  | `default_phone_code` | `+1` | Used when a relative's phone number is typed without a country code (§13.11). |
  | `tithi_rule` | `aparahna` | Which day a tithi is observed: `aparahna` (the tithi covers the afternoon) or `sunrise` (§13.12). |
  | `map_tiles_url` / `nominatim_url` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` / `https://nominatim.openstreetmap.org` | Places map (§13.3): http(s) only, no spaces or quotes; tiles need `{z}` `{x}` `{y}` (`{s}` allowed). The tile server's origin is added to the CSP `img-src` only while the map is on. |
  | `feature_<name>` | see §3.1 | The feature switches. |

  The page shows each module's own settings only while that module is switched on (phone code with contacts, tithi day with tithi, relationship language with Indian relationship names, map addresses with the map).
- A backup restore brings back the backup's App settings except `media_path`, which belongs to the install (like `media_store_id`) and is always kept; a backup from an older database with no App settings keeps the ones in use (its feature switches follow its own data, §3.1).

### 3.1 Feature switches (`features.py`)
Every optional module has a switch, the App setting `feature_<name>` (bool), shown in **Admin → App settings → Features** grouped as general, region/culture-specific and internet-using, each with its help text and default.

| Switch | New install | Module | Data that turns it on in an existing database |
|---|---|---|---|
| `feature_reminders` | on | Reminders (§9): 🔔 switches and bells, Settings → Reminders, phones/notify services in Admin → Users, the digest loop | `people.remind = 1`, enabled `reminder_prefs`, `user_notify` rows |
| `feature_milestones` | on | Milestones (§13.15) | — |
| `feature_sides` | on | Father's/mother's side (§13.16) | — |
| `feature_photo_tagging` | on | Face boxes (§13.2) | `media_regions` rows |
| `feature_photo_fixes` | on | Photo fixes (§13.19) | `media.edit` set |
| `feature_inbox` | on | Inbox folder and Unsorted queue (§13.10) | unsorted or inbox-imported media |
| `feature_custom_fields` | on | Custom fields (§13.8) | `custom_fields` rows |
| `feature_sources` | on | Sources and citations (§13.9) | `sources` / `citations` rows |
| `feature_contacts` | on | Contacts, address book, vCard (§13.11) | `contacts` rows |
| `feature_duplicates` | on | Duplicate finder and merge (§13.4) | `not_duplicates` rows, merged people |
| `feature_quiz` | on | Photo quiz and kids mode (§13.13) | `quiz_stats`, `kid_sessions`, a kids-mode PIN |
| `feature_printing` | on | Wall chart and family book (§13.5) | — |
| `feature_export` | on | Website export (§13.6) | `export_presets`, `users.export_last` |
| `feature_map` | **off** (internet) | Places map and geocoding (§13.3) | `place_geo` rows (and the older `enable_map` setting's value) |
| `feature_kin_names` | **off** (regional) | Indian relationship names (§13.1) | `kin_terms` rows, `users.kin_lang` te/hi, `relationship_language` ≠ en |
| `feature_script_names` | **off** (regional) | Names in Telugu/Hindi script (§13.7) | `given_local`/`surname_local` set, `users.name_display` script/both |
| `feature_tithi` | **off** (regional) | Tithi / Hindu lunar calendar (§13.12), incl. the full-moon milestone | `event_tithi` rows |
| `feature_ceremonies` | **off** (regional) | Indian ceremony event types (§13.14) | events of a ceremony type |

- **Existing databases** (older installs, restored backups): `db._migrate` runs, for each switch, `INSERT OR IGNORE INTO app_settings … SELECT 'feature_x', 'true', … WHERE EXISTS (<data>)`, so a module that already holds data comes up on; a stored value always wins. The older `enable_map` / `inbox_enabled` settings are carried into `feature_map` / `feature_inbox` and removed.
- **Off = hidden, not deleted.** `features.on(name)` is read live. While a switch is off:
  - **API:** the module's routes (whole routers, or single routes via `dependencies=[Depends(features.required(name))]`) raise `FeatureOff`, which `main.py` answers with 404 `{"detail": "<Label> is turned off. …", "featureOff": name}` — admins are pointed to Admin → App settings → Features. Module fields sent to shared routes (script names or `remind` on a person, a ceremony event type, a region as profile photo, `side`/`remind`/`field` filters, `unsorted` media) get the same 404.
  - **Payloads:** person details, summaries and cards drop the module's data (`nameLocal`, `custom`, `cardFields`, `citations`, `contacts`, `tithi`, `remind`, regions, sides); ceremony events are left out of person/family details, timelines and map points; the relationship language is English (`kin.current()` / `kin.user_lang()`).
  - **Page:** `/me` returns `features` (`{name: bool}`); navigation items (`data-feature`), More tiles, buttons, form fields, filters, cards and badges skip what's off; a route of a switched-off module shows "… is turned off" (with a link for admins).
  - **Background jobs:** the reminder pass, geocoding and the inbox scan skip; tithi entries and the full-moon milestone aren't computed; a device in kids mode is released while the quiz is off (the session is kept).
  - **Exports** (`export_view.build`): contacts, sources, custom fields and ceremony events are left out even when saved options ask for them.
  - Turning a switch back on shows everything again (nothing was changed or deleted).

## 4. Users, security & privacy
- **Users.** Every `person.*` linked to an HA user is synced, like Household Vault. Users are **enabled by default**; an admin disables anyone. A disabled user gets 403 everywhere and no reminders. Someone first seen through ingress is added automatically.
- **Identity** follows the siblings: only requests from the ingress proxy are accepted, the user is identified by `X-Remote-User-Id`, and admins are matched by id or login name. There's no "acting as": edits are always recorded as the real person.
- **Who can do what.**
  - Every enabled user: view everything; add, edit and delete people, families, events and media (deletes go to the trash); undo changes, including deletes; claim "This is me" for one unclaimed person; set up their own reminders.
  - Admins additionally (the **Admin** area): change App settings and feature switches, disable users, change anyone's "This is me", see the trash, restore from it and empty it, and back up or restore.
- **Media safety.**
  - **Accepted types:** JPEG, PNG, WebP, HEIC (converted to JPEG) and GIF (first frame) as images; PDF as documents. The type is checked by **magic bytes**, never trusted from the name or header. SVG, HTML and anything scriptable are rejected.
  - **Images are re-encoded** by Pillow on upload. This strips metadata, including **GPS/EXIF**; the original date taken is copied to the media record first. They are capped at 4096 px on the long edge, with thumbnails at 256 px and 1024 px.
  - **Serving:** files are served only through `/api/media/{id}/file` with the stored content type, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline` for images and `attachment` for PDFs.
- **Browser:** the same CSP as Household Vault (without the wasm allowance), DOM building only (never `innerHTML` with user text), and `rel="noopener noreferrer"` on links.
- **Living people.** A person is *living* unless they have a death date or death flag, or were born more than 110 years ago. A birth date without a year never makes someone "too old", so they count as living unless marked deceased. This affects only:
  - reminder eligibility;
  - export privacy (§8);
  - ages, shown as "age 42" for the living and "aged 87" at death.

## 5. Data model (SQLite `/data/family.db`; media in `<media_path>`, §5.1)
```sql
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT, ha_person TEXT,
  disabled INTEGER NOT NULL DEFAULT 0, me_person_id TEXT REFERENCES people(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL);
CREATE UNIQUE INDEX idx_me ON users(me_person_id) WHERE me_person_id IS NOT NULL;   -- one claim per person

CREATE TABLE people (id TEXT PRIMARY KEY,
  given_names TEXT, surname TEXT, birth_surname TEXT,        -- birth/maiden surname
  nickname TEXT, other_names TEXT,                           -- JSON [{type: aka|married|religious|…, name}]
  gender TEXT NOT NULL DEFAULT 'unknown' CHECK (gender IN ('female','male','other','unknown')),
  deceased INTEGER NOT NULL DEFAULT 0,                       -- set when death is known without a date
  photo_media_id TEXT REFERENCES media(id) ON DELETE SET NULL,
  biography TEXT,                                            -- plain text, ≤ 20 000 chars
  remind INTEGER NOT NULL DEFAULT 0,                         -- 🔔 reminders for this person (§9); off by default
  gedcom_id TEXT, gedcom_extra TEXT,                         -- reserved for a GEDCOM import (§14)
  created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT);

CREATE TABLE families (id TEXT PRIMARY KEY,
  partner1_id TEXT REFERENCES people(id), partner2_id TEXT REFERENCES people(id),   -- either may be NULL
  kind TEXT NOT NULL DEFAULT 'married' CHECK (kind IN ('married','partners','unknown')),
  ended TEXT CHECK (ended IN ('divorced','separated','widowed')),                  -- NULL = ongoing / n.a.
  gedcom_id TEXT, gedcom_extra TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
  CHECK (partner1_id IS NOT NULL OR partner2_id IS NOT NULL));
CREATE TABLE family_children (family_id TEXT NOT NULL REFERENCES families(id) ON DELETE CASCADE,
  child_id TEXT NOT NULL REFERENCES people(id), position INTEGER NOT NULL DEFAULT 0,
  relation TEXT NOT NULL DEFAULT 'birth' CHECK (relation IN ('birth','adopted','step','foster','unknown')),
  PRIMARY KEY (family_id, child_id));

CREATE TABLE events (id TEXT PRIMARY KEY,
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE, family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  type TEXT NOT NULL,        -- birth death burial baptism marriage engagement divorce education occupation
                             -- residence immigration military religion retirement custom
  title TEXT,                -- for 'custom', or e.g. the school / employer
  date_text TEXT,            -- GEDCOM-style: "12 MAR 1950", "ABT 1950", "BET 1900 AND 1910", "BEF 1920"
  date_y INTEGER, date_m INTEGER, date_d INTEGER,           -- parsed known parts; year may be NULL with m+d set
                                                            -- (birthday known, year not); reminders need m+d
  date_approx INTEGER NOT NULL DEFAULT 0, sort_key TEXT,    -- best-estimate ISO date for ordering
  place TEXT,                -- "City, State, Country" free text (≤ 200)
  description TEXT,          -- ≤ 2 000
  gedcom_extra TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK ((person_id IS NULL) <> (family_id IS NULL)));
CREATE UNIQUE INDEX idx_one_birth ON events(person_id) WHERE type = 'birth';
CREATE UNIQUE INDEX idx_one_death ON events(person_id) WHERE type = 'death';

CREATE TABLE media (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('photo','document')),
  title TEXT, description TEXT, date_text TEXT, content_type TEXT NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, width INTEGER, height INTEGER,
  created_by TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE media_links (id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  person_id TEXT REFERENCES people(id), family_id TEXT REFERENCES families(id), event_id TEXT REFERENCES events(id),
  CHECK ((person_id IS NOT NULL) + (family_id IS NOT NULL) + (event_id IS NOT NULL) = 1));

CREATE TABLE stories (id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  title TEXT NOT NULL, body TEXT NOT NULL,                  -- plain text, ≤ 50 000 chars
  created_by TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE batches (id TEXT PRIMARY KEY, user_id TEXT, label TEXT NOT NULL,   -- one user action = one batch
  undo_of TEXT, undone_by TEXT, created_at TEXT NOT NULL);                     -- "Added Subba Rao as a parent of Lakshmi"
CREATE TABLE changes (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL, user_id TEXT, entity TEXT NOT NULL, entity_id TEXT NOT NULL,   -- entity_id "fam|child" for links
  op TEXT NOT NULL CHECK (op IN ('create','update','delete','restore','link','unlink')),
  before TEXT, after TEXT,                                  -- JSON row snapshots
  created_at TEXT NOT NULL);
CREATE TABLE batch_people (batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL, PRIMARY KEY (batch_id, person_id));                 -- a person's History tab

CREATE TABLE reminder_prefs (user_id TEXT PRIMARY KEY REFERENCES users(id),
  enabled INTEGER NOT NULL DEFAULT 0, send_time TEXT NOT NULL DEFAULT '08:00',   -- the user's own time
  birthdays INTEGER NOT NULL DEFAULT 1, anniversaries INTEGER NOT NULL DEFAULT 1,
  remembrance INTEGER NOT NULL DEFAULT 0,                    -- birthdays/death dates of the deceased
  lead_days INTEGER NOT NULL DEFAULT 0 CHECK (lead_days BETWEEN 0 AND 14),
  scope TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('close','all')));   -- close = within 3 steps of "me"
CREATE TABLE reminder_log (user_id TEXT NOT NULL, sent_on TEXT NOT NULL, PRIMARY KEY (user_id, sent_on));
CREATE TABLE user_notify (user_id TEXT NOT NULL REFERENCES users(id), service TEXT NOT NULL,   -- "notify.x", set by admins
  added_by TEXT, added_at TEXT NOT NULL, PRIMARY KEY (user_id, service));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);   -- e.g. media_store_id (§5.1)
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,  -- App settings and feature switches (§3); value is JSON
  updated_at TEXT NOT NULL, updated_by TEXT);                           -- admin's HA user id
```
- **Deletes are soft** (`deleted_at`), so deleted things vanish from every view but stay restorable. A daily purge removes them after `trash_days` (an App setting, §3), along with their media files. Hard FKs only cascade when something is purged.
- **Dates** (`dates.py`) accept GEDCOM phrases and friendly input: "1950", "Mar 1950", "12/03/1950" (in the HA locale order), "about 1950", "before 1920", "between 1900 and 1910". They're stored as canonical `date_text` plus parsed parts and a `sort_key`.
- **Day and month without a year** (e.g. a birthday you know but not the year it was) is allowed for **any** event, with birthdays and anniversaries as the main use.
  - Stored as `date_y = NULL`, `date_m`, `date_d` and `date_text = "12 MAR"`.
  - Shown as "12 March".
  - Reminders still work; they just leave out the age or the number of years (§9).
  - The `sort_key` is NULL, so these sort after dated items, ordered by month and day.
  - Ages, the living-person rule, consistency warnings and the timeline treat the year as unknown.
  - **Accepted combinations:** year only; month + year; day + month + year; **day + month**. A day without a month is rejected (422), and so is a month alone. 29 Feb without a year is allowed.
- **Consistency warnings** are shown in the UI but never block a save: a child born before a parent, a parent younger than 12 at the birth, death before birth, age over 110 while "living", or a person in a loop (their own ancestor). **An edit that would create a loop is rejected (422).**
- **Limits:** 20 000 people, and 50 events and 200 media links per person.

### 5.1 Media storage on `/share`
- **Why.** Photos and documents are almost all of the data. Keeping them out of `/data` means the app's own backup holds only the database (a few MB), however many photos you add.
- **Layout** under the photo folder (App setting `media_path`, default `/share/family_tree`; `media.root()` reads it live):
  - `media/<id[0:2]>/<id>/{original,thumb1024,thumb256}`. The two-letter fan-out keeps folders small.
  - `.exports/` for export jobs (§13.6.1), cleaned hourly.
  - `.family_tree_store`, a marker file holding this tree's store id (also saved in the DB as `settings.media_store_id`).
  - The database stores **ids only, never paths**, so the folder can move.
- **Keeping photos out of HA backups.**
  - The app's backup, and "Family Tree" in a partial backup, contain only `/data`.
  - HA **full and automatic backups include the Share folder** unless it's unticked. Two ways to keep photos out:
    1. **Recommended: a NAS.** Add network storage in HA (Settings → System → Storage → *Add network storage*, usage *Share*); it appears as `/share/<mount name>`. Copy the folder there and set the photo folder to `/share/<mount name>/family_tree` in Admin → App settings. HA doesn't copy network-storage mounts into its backups, and the NAS's own snapshots protect the photos.
    2. **Local `/share`:** untick *Share folder* in the automatic backup settings. This leaves out everything in `/share`, not just photos.
  - DOCS.md explains both. The Storage tab shows the media path, whether it's a network mount, the file count, total size and free space, and a reminder that **photos need their own backup** when they're outside HA's.
- **When the share is unavailable** (NAS off, mount failed):
  - At startup, every 5 minutes, before any media write, and right after the photo folder is changed, the app checks that the folder exists, is writable, and has a marker matching `media_store_id`. If not, **media goes offline**. The online state remembers which folder it was checked for: after a change nothing counts as online until the new folder has been checked. The database is read in short connections, never held during file-system work on the (possibly slow) share. The app never writes into an unmounted folder, because those files would land on HA's own disk and be hidden once the mount returns.
  - While offline, the tree, people and history all work. Photos show initials placeholders. Uploads, media deletes and media trash purges wait, behind a banner: "Photo storage at /share/nas/family_tree isn't reachable".
  - **First start:** an empty or missing folder is created along with its marker — unless the database already has media rows (then it stays offline: a fresh marker must never make an empty folder look like the tree's photos). A folder with a *different* marker ("belongs to another Family Tree") is refused until an admin chooses *Use this folder* (for example after restoring the database) or changes the photo folder.
- **Media check** (Storage tab, admin): lists media rows whose files are missing, and files on disk that aren't in the database. Orphans can be moved to `orphans/`; nothing is deleted automatically.
- **Changing the folder** (Admin → App settings; no restart, files never moved). **Check folder** (`POST /admin/settings/check-media-path {path}`, admin-only, no side effects) reports `{exists, writable, marker: none|this|other, files (originals found), dbMedia, verdict, message, ok, needsConfirm, refused}` with verdicts *current*, *this* (safe), *new* (tree has no media: created/set up on save), *no_marker* (tree has media: "Your N photos stay in the old folder. Copy the whole folder, including .family_tree_store, to the new place first…"), *other* (another tree's marker: refused), *read_only* and *missing_parent*. `PUT /admin/settings` enforces the same: *other* → 409 always; *no_marker* → 409 unless `"confirm": true`. After saving, the check re-runs at once (`check(allow_setup=True)`): a folder without a marker is set up with this store's id only while the tree has no media; otherwise it comes up offline with a reason ("copy the whole old folder there, including .family_tree_store"), and the banner shows it. Everything that uses the root follows the new folder: uploads, file serving, thumbnails, purge, `.exports/` and its cleanup (running export jobs keep the file path they already wrote to), backups with photos, restore with photos, media check / adopt / orphans, and the Storage tab.
- **Moving the photos:** copy the whole folder including the marker, then change the photo folder in Admin → App settings.
- **Visibility:** anything with access to `/share` can read the photos, such as the Samba app or anyone on a NAS share. GPS and EXIF are already stripped on upload (§4). Notes, stories and every other record stay in `/data`.

## 6. Relationships (`relations.py`)
- **Graph:** parents come from `family_children` → the family's partners; partners come from shared families; siblings share a family (half-siblings share only one parent). Adopted, step and foster links count as parent links, and their labels say so ("adoptive mother").
- **"Relationship to you"** is computed between the viewer's `me_person_id` and any person:
  1. BFS upward from both people, stopping after 12 generations, to find the lowest common ancestors.
  2. Name the relationship from the generation counts (g1, g2): parent/child, grandparent (great-×n), sibling/half-sibling, aunt/uncle (grand-/great-), niece/nephew, and cousins "nth cousin k times removed".
  3. Otherwise, one partner hop on either side gives the in-law forms: spouse, parent-/sibling-/child-in-law, and "spouse of your cousin".
  4. Otherwise, "related through <name>" or "not related".
- Wording is gendered when gender is known ("aunt"/"uncle") and neutral otherwise ("parent's sibling").
- **Kinship path:** "How are we related?" shows the chain of people between you and them.
- **Any two people:** More → *How are they related?* (route `#/relate/<a>/<b>`, also from a person page's "🔗 Related to someone else?") picks any two people and shows the relationship both ways plus the chain, using GET `/relationship?from=&to=` twice (the answer is what `to` is to `from`, in the viewer's relationship language, the kin term chosen by `from`'s gender). Neither person needs to be the viewer's "This is me".
- The same code produces the **"close family"** reminder scope: everyone within 3 relationship steps, where a parent, child, sibling or partner each count as one step.

## 7. Change history & undo (`history.py`)
- **Recording.** Every write goes through one helper that records row snapshots in `changes`, grouped by `batch_id`; for example, "Add child" creates a person, a birth event and a family link together.
- **Undo.** You can undo a batch if nothing it touched has changed since (otherwise 409, with "undo the later change first"). Undo writes its own batch, so it can be undone too.
- **Visible history.** Each person's page has a History tab, and the global **History** view lists who changed what, filterable by person and user.
- **Trash** (Admin → Trash, admins only ). Deleted people, families and media, each with Restore, and Empty trash. Everyone else gets a delete back with Undo (the toast, or History).
- History is kept forever (it's small); the database backup includes it.

## 8. Export (`export_view.py`, `site_export.py`)
- **Export** (any user, while the Website export switch is on): a standalone family **website** (§13.6.2). The **wall chart** and **family book** (§13.5, Printing switch) are rendered in the browser from the same projection.
- **Every export uses the Export options** (§13.6.1): who is included and which details go out (dates, places, photos, stories, notes and so on). The **living-people rule** is part of it and defaults to *limited*: living people get name and relationships only, with no dates, places, photos or notes. Alternatively the export can include everything, or skip living people entirely.
- **Enforced on the server, in one place:** `export_view.build(options)` applies scope, the keep-out flag (§13.20), the living rule, the detail choices and the feature switches (§3.1), so a switched-off detail can't leak through any format.
- Relationship labels in exports are in English.

## 9. Reminders (`reminders.py`, `ha_notify.py`, `ha_people.py`)
A reminder about someone goes out only when **two switches are on**: the person's own **🔔 Reminders** switch in the tree, and the user's own reminders in Settings. Both are **off by default**, so nobody gets anything, and nobody is reminded about, until someone chooses to.

**Per-person switch (`people.remind`)**
- **Off by default for every person**: people added by hand, by quick adds and **+ Family**. Existing people are off after the upgrade that adds the column.
- **One switch per person, shared by the household**, not per user: if Lakshmi's switch is on, her birthday can appear in the digest of everyone who has reminders on (subject to their own choices below). Any enabled user can change it; it's an ordinary person edit, so it's in History with who changed it, and it can be undone.
- **Where it's changed:**
  - the person page header: a 🔔 / 🔕 toggle button with the label "Reminders on" / "Reminders off" (tapping it saves straight away with the usual Undo toast);
  - Edit person → More details: a "Send reminders for this person" checkbox;
  - the Upcoming view: each entry shows 🔔 when reminders are on for it, and a small bell button to switch it on or off right there — the Upcoming view itself still lists everyone (it's a calendar, not a notification);
  - the People list: a filter *Reminders: on / off / any*, so the household can see who is covered;
  - Settings → Reminders: a shortcut **"Turn on reminders for my close family (N people)"** (within 3 steps of "This is me", living and deceased), with a confirmation that lists how many will change; one history batch, undoable in one go. There's no "everyone" shortcut, on purpose.
- **What it covers:** every reminder kind about that person — 🎂 their birthday, 🕯 their remembrance days, their tithi (§13.12) and milestone alerts (§13.15). A couple's 💍 **anniversary** is sent when **at least one** of the two partners has reminders on.
- **What it doesn't affect:** the Upcoming view (lists everyone), exports (not exported, including the website), and *Keep out of all exports* (§13.20), which is independent. People in the trash are never reminded about, whatever their switch says; restoring them brings the switch back as it was.

**Per-user settings** (Settings → Reminders; `reminder_prefs`)
- **Reminders on/off** (off by default). It can only be switched on when something reaches you: a phone linked to you in Home Assistant or an extra notify service an admin assigned; otherwise the card says to pick your phone in Home Assistant → Settings → People → you → Track device.
- **Time** (default 08:00, the user's own; there is no household default), **birthdays** (on), **anniversaries** (on), **remembrance** (off), **days ahead** 0–14 (0 = only today), and **scope**: `all` (the default: everyone whose 🔔 switch is on) or `close` (only those within 3 steps of "This is me", which needs "This is me" set).
- **Send me a test** sends one short notification to your phones and all your extra notify services.

**Notify services** (admins, Admin → Users)
- Each user row first shows their **Phones** from Home Assistant (read-only, `ha_people.py`, see "Phones from Home Assistant" below); the assigned services below are *extras* ("Also").
- Each user row shows their extra notify services as removable chips, a list of the services Home Assistant offers (`GET /api/services`, domain `notify`, plus notify entities; cached 60 s; if HA can't be reached the admin can type a name), **Add** and **Send test**. Several services per user; names are validated as `notify.` + `[a-z0-9_]+`. Stored in `user_notify`.

**The daily digest**
- Sent once a day in the hour after the user's time, in HA's time zone (`reminder_log`, survives restarts), and **only when there's something to say**. It covers today and the next *days ahead*:
  - 🎂 **Birthdays** of *living* people with a known day and month: "🎂 Lakshmi (your aunt) turns 60 today", or "…turns 60 on Fri 3 Oct". If the birth year is unknown, the age is left out: "🎂 It's Lakshmi's birthday today".
  - 💍 **Anniversaries** of marriages with a known day and month, when the family hasn't ended and both partners are living: "💍 Ravi & Priya — 25th anniversary today" ("…anniversary today" when the year is unknown).
  - 🕯 **Remembrance** (if the user turned it on): the birthdays and death dates of deceased people. "🕯 Remembering Grandpa Venkat, born 100 years ago today".
- **Who is in it:** people whose 🔔 switch is on (for anniversaries: either partner), not in the trash, within the user's scope, and matching the kinds the user chose. A disabled user gets nothing.
- **Content rule:** names, relationship and age only — never biographies, notes or places. Relationship labels are included when "This is me" is set. Title "Family Tree"; one line per item; long lists end with "…and N more" so it fits a phone notification.
- **29 February** birthdays are marked on 28 February in non-leap years.
- No calls to Home Assistant while holding a database connection; the pass runs in the background loop.

**Details:** - The digest reuses `upcoming.entries()` (each entry carries `remind`: the person's switch, or either partner's for an anniversary) and filters it by 🔔, the user's kinds and scope. `scope: close` without a live "This is me" sends nothing (never silently everyone); Settings shows a warning.
- Lines: "🎂 Lakshmi Sharma (your mother) turns 60 today" · "🎂 It's Bob Jones's birthday tomorrow" · "🎂 Cat Jones turns 26 on Thu 1 Oct" · "💍 Ravi & Priya — 25th anniversary today" · "🕯 Remembering Venkat, born 100 years ago today" · "🕯 Remembering Venkat, who died 25 years ago tomorrow" (no years: "— their birthday, today" / "— the day they died, today"). At most 8 lines, then "…and N more". Title "Family Tree".
- A separate asyncio loop ticks every 60 s (`reminders.loop()`, started in `main.py` next to housekeeping): it reads due users, prefs, services and the graph in one short connection, closes it, sends to **every** service of the user, then logs `(user, day)` if at least one accepted. A failed send isn't logged, so it's retried each minute while the hour lasts. `reminder_log` rows older than 60 days are pruned daily. At start-up, assigned services Home Assistant can't reach are logged once (`check_targets_blocking`).
- "Close family" (the shortcut and `scope: close`) is `upcoming.close_family(g, me, 3)` and includes the user's own person.
- The per-person switch is `remind` in `PersonIn`, **ignored on create** (every new person starts off). A PATCH that changes only `remind` is labelled "Turned reminders on/off for <name>"; several people change in one batch through PUT `/reminders/people` (the Upcoming bell for a couple) and POST `/reminders/close-family` ("Turned reminders on for close family (N people)").
- Undo compares only the columns an old snapshot knew about, so a batch recorded before `people.remind` existed stays undoable.

## 10. API (all under ingress; `/api`; writes return the updated object + `batchId`)
| Area | Routes |
|---|---|
| Me & users | GET `/me` (`{id, name, isAdmin, mePersonId, maxUploadMb, trashDays, kinLang…, mapEnabled, features: {name: bool}, noAdmin, media}`) · PUT `/me/person` `{personId\|null}` (claim; 409 if already claimed) · GET `/users` · admin: GET `/admin/users`, PATCH `/users/{id}` `{disabled?, mePersonId?}` |
| People | GET `/people?q=&living=&surname=&sort=&page=` · POST `/people` · GET/PATCH/DELETE `/people/{id}` (GET includes parents, partners+families, children, siblings, events, media, stories, `relationshipToMe`, `warnings`) · admin: POST `/people/{id}/restore` (also `/families/{id}/restore`) |
| Quick adds | POST `/people/{id}/add-parent` · `/add-partner` · `/add-child` `{person:{…}\|existingId, familyId?, relation?}`, one batch each |
| Families | POST `/families` · PATCH/DELETE `/families/{id}` · POST/DELETE `/families/{id}/children[/{childId}]` · PATCH `/families/{id}/children/{childId}` `{relation, position}` |
| Events & stories | POST `/events` · PATCH/DELETE `/events/{id}` · GET/POST `/people/{id}/stories` · PATCH/DELETE `/stories/{id}` |
| Media | POST `/media` (multipart: file + optional personId/familyId/eventId/title, one file per request, 413/415) · GET `/media?personId=&familyId=&kind=&q=&unlinked=&sort=added\|date&page=` · GET/PATCH/DELETE `/media/{id}` (PATCH title, description, date) · POST `/media/{id}/links` · DELETE `/media/{id}/links/{linkId}` · GET `/media/{id}/file?size=256\|1024\|original` · POST/PUT/DELETE `/people/{id}/photo` (upload new / choose existing / clear the profile photo) |
| Tree | GET `/tree/all` (everyone, §11) · GET `/tree?focus=&up=4&down=3&siblings=1` → `{nodes, families, edges}` for the chart (up ≤ 10, down ≤ 10, ≤ 2 000 nodes) · GET `/relationship?from=&to=` → `{label, path}` |
| Upcoming | GET `/upcoming?days=60&scope=close\|all` → birthdays, anniversaries and remembrance entries for the calendar view |
| History | GET `/history?personId=&userId=&page=` · POST `/history/{batchId}/undo` · admin: GET `/trash` (`{items, trashDays}`) · POST `/trash/{entity}/{id}/restore` · DELETE `/trash` |
| Export (any user) | POST `/export/preview` `{format: site, options}` → `{people, living, media, bytesEstimate, warnings}` · POST `/export` → `{jobId}` · GET `/export/{jobId}` → `{status, progress}` · GET `/export/{jobId}/file` · GET/PUT/DELETE `/export/presets[/{id}]` — §13.6 |
| Reminders | GET/PUT `/reminders/prefs` (GET adds `services`, `canEnable`, `meSet`, `peopleOn`, `closeFamily {total, off}`, `timezone`, `warning`; PUT 409 when turning on without a service, 422 for `close` without "This is me") · POST `/reminders/test` (10 s rate limit) · GET `/reminders/close-family` → `{meSet, total, off}` · POST `/reminders/close-family` (turn 🔔 on for close family; returns the count and `batchId`, which is null when nothing changed) · PUT `/reminders/people` `{ids (≤ 50), remind}` (one batch) · the per-person switch is `remind` in PATCH `/people/{id}` and `?remind=on\|off` in GET `/people` · admin: GET `/admin/notify-services` · GET `/admin/users?refresh=1` re-reads HA's people first (rows include `ha`, §9) · POST/DELETE `/admin/users/{id}/notify[/{service}]` · POST `/admin/users/{id}/notify-test` |
| Modules (§13.7–13.20) | CRUD `/custom-fields`, `/sources`, `/citations`, `/people/{id}/contacts` · GET `/contacts?scope=` + `/contacts.vcf?ids=` · GET `/media?unsorted=1` + POST `/inbox/scan` · PUT `/events/{id}/tithi` + GET `/tithi/dates?year=` + PUT `/tithi/{eventId}/{year}` (override) · GET `/quiz/next?mode=&player=` + POST `/quiz/answer` · POST `/kidmode/start` · GET `/kidmode` · GET `/kidmode/challenge` · POST `/kidmode/unlock` · admin: DELETE `/kidmode/{deviceId}` · PUT `/me/kid-pin` · GET `/people/related?anchor=&rel=&side=&living=` · POST `/families/quick` · PUT `/media/{id}/edit` · CRUD `/milestones` (admin) |
| Relationship names | PUT `/me/kin-lang` `{kinLang: en\|te\|hi\|null}` (null = App setting; `/me` returns `kinLang`, `kinLangEffective`, `kinLangApp`) · GET `/kin-terms?lang=te\|hi` → `{items: [{key, meaning, term, note, source, seedTerm}]}` · PUT `/kin-terms/{lang}/{key}` `{term, note}` · DELETE `/kin-terms/{lang}/{key}` (reset) — each a history batch. `relationshipToMe` and GET `/relationship` add `{english, term, termKey, kinKey, meaning, ageUnknown, note}`; `label` is the term when there is one |
| Sides & search | `sides=true` on GET `/tree` (relative to "me", else the focus; `sideAnchor`) and GET `/tree/all` (relative to "me") adds `side` to cards · GET `/people?side=paternal\|maternal` (needs "me"; `both` counts for either) · GET `/people/related?anchor=&rel=&term=&side=&living=&generation=&gender=` → `{anchor, items: [summary + relationship, english, generation, side]}` · GET `/people/related/parse?q=` → `{anchor, anchorName, rel, gender, side, term, understood}` (422 with a hint) |
| Whoami | GET `/whoami` → `{haUserId, haUsername, haDisplayName, nameSent, isAdmin, displayNameOnly, adminEntries, mePersonName, notifyLinked, remindersOn, remindPeople, disabled, noAdmin, adminOption}` (counts only; also works for disabled users) |
| Admin | GET/PUT `/admin/settings` → `{values, defaults, meta: {key: {restartRequired}}, features: [{key, name, label, default, kind, help}], media: {path, online, reason}}` (PUT takes any subset plus `confirm`; 422 for out-of-range, wrong type or unknown keys; 409 for a photo folder that is refused or needs `confirm`, §5.1) · POST `/admin/settings/check-media-path` `{path}` · GET `/admin/users` · GET `/admin/storage` · GET `/admin-storage-download-db` (a zip of `family.db` + media) · POST `/admin-storage-import-db` (validated, re-runs `init_db()`) — §11.1 |

## 11. Frontend
- **Tree** (the start view). An **hourglass chart**: the focus person in the middle, ancestors above (default 4 generations), and descendants below (3). Partners sit beside each person and siblings can be toggled on.
  - **Cards** show the photo, name and years ("1950–2019"), with a sex colour accent and a 🕯 for the deceased.
  - **Interaction:** tap a card to open the person; double-tap to re-centre; pinch, drag and wheel to zoom and pan; "Me" re-centres on you. Generation depth is adjustable.
  - **Adding relatives:** empty "+ Add father / mother / partner / child" ghost cards.
  - **Print / save as PDF:** a print stylesheet of the current view.
  - **Layout:** its own layered layout. Each generation is a row, couples are grouped, children are centred under their family, and subtrees never overlap.
  - **Everyone view** (Chart / Everyone / Cards switch, remembered per browser): the whole tree at once from GET `/tree/all` (people, families and a generation per person; parents one row up, children one down, partners level; where data disagrees the first assignment wins). The browser groups partners into units, orders each row by a depth-first walk (a married couple sits under the family it was reached from, so in-laws' parents move towards them), then sweeps up and down pulling units under their parents and over their children, placing each row with an isotonic fit so nothing overlaps. Unconnected groups sit side by side; "Find someone" pans to a person; double-tap opens their close-up chart. Up to 3 000 people (relationship labels up to 800).
- **Person page.**
  - A header with photo, names, dates, age, "your first cousin" and the kinship path.
  - Tabs: **Overview** (facts and family lists, each clickable), **Timeline** (events in date order, including relatives' births, marriages and deaths during their life), **Photos & documents**, **Stories**, and **History**.
  - Warnings are shown as small notices.
- **Date entry** (birth, death and every event): three fields — **Day**, **Month** (a dropdown) and **Year** — plus an "About / Before / After / Between" qualifier.
  - **Year is optional.** Leaving it empty with day and month filled means "birthday known, year unknown". The date then shows as "12 March", and ages show as "age unknown".
  - A hint under the field says reminders will still arrive, without the age.
  - Typing a whole date into the day field ("12/03/1950") fills all three.
- **People list:** search (names, including maiden and nicknames), filters (living/deceased, surname, has photo), and sort (name, birth date, recently changed).
- **Upcoming:** this month and next — birthdays, anniversaries and remembrance, with relationship labels.
- **Media:** a gallery with a filter by person, upload (drag-and-drop, several files at once), tagging people, and choosing the profile photo.
- **History** (see §7). **Export** (§13.6.1). **Settings:** "This is me", names and relationships, reminders, milestones, custom fields (admins), the kids-mode PIN, display (theme, typed date order) and the whoami card — each card only while its module is on.
- **🛡️ Admin**: one sidebar item, shown to admins only, opening `#/admin/settings`. Tabs **App settings** (§3: number fields with range and default; the photo folder with **Check folder**, the result box, the folder in use now with its online state, and a confirm dialog before saving a folder that isn't this tree's; Save), **Users** (enable/disable, set/unset "me"), **Storage** (backup/restore) and **Trash** (§7), deep-linkable as `#/admin/settings|users|storage|trash`; the tab row wraps on phones. The old `#/trash` link redirects to `#/admin/trash`. A non-admin opening any of these sees "Only admins can open this page" (the server answers 403 anyway). Delete confirmations tell non-admins to use Undo/History, since they can't see the trash.
- **Phone layout:** the sibling apps' bottom icon bar. On phones the tree gets a simplified vertical "family card" mode: parents, then the person, then partners and children, with swipe navigation.

### 11.1 Features shared with the other apps
- **Themes.** Four themes defined with CSS variables on `[data-theme]`: **Heritage** (warm sepia on dark, the default), **Slate**, **Daylight** and **Parchment** (light and warm), plus **Auto**, which follows the device's light/dark setting.
  - Picked in the sidebar footer (and in Settings on phones), and remembered per browser in `localStorage.theme`.
  - Applied **before first paint** by an external `static/theme-boot.js` (the CSP forbids inline scripts).
  - The tree chart reads the same CSS variables, so cards and lines follow the theme. Printing always uses Daylight.
  - The collapsible sidebar is remembered in `localStorage.sidebarCollapsed`.
- **"How the app sees you" page** (`WHOAMI_PAGE_SPEC.md`), opened from the name chip at the bottom of the sidebar (👤 on phones), for the real signed-in person. It shows:
  - user name, user id (with copy buttons) and display name ("not used for matching");
  - **Administrator in this app**, and **Names in `admin_users`** (a count only);
  - family-tree rows: **This is me** (the linked person's name, or "not set" with a link to set it), **Reminders** (how many phones from Home Assistant and extra notify services reach you, whether your reminders are on, and how many people have 🔔 on; how to link your phone in Home Assistant when there are none), and **Account status**.

  Advice text explains any mismatch.
- **Admin export / import** (Storage tab, admins only; routes `admin-storage-download-db` / `admin-storage-import-db`, as in the siblings):
  - **Export:** `family-tree-backup-YYYYmmdd-HHMMSS.zip` holding a consistent `family.db` snapshot (`sqlite3` backup API), including history and trash. Two choices:
    - **Database only** (the default, small): every record, but no photo files. The photos stay safe on the share.
    - **Database + media** (large, streamed): also every file from `media_path`, for a complete copy or a move to another HA.
  - **Import:** upload a zip. It is validated before anything is touched: the zip layout, no path traversal, `PRAGMA integrity_check`, the required tables, and any included media files of allowed types. Then it replaces the database, writes any included media to `media_path`, re-runs `init_db()`, and logs a history entry "Restored from backup".
  - A database-only restore is fine when the photos are still on the share. Media the database references but can't find is reported ("37 photos missing") and shown as placeholders, but doesn't block the restore.
  - The Storage tab notes that a website export is **not** a backup (no history, only the details you chose).

## 12. Tests
`tests/` (unittest, `TestClient(app, client=("127.0.0.1", 12345))`; `python -m unittest discover -p "test_*.py"` from `tests/`). The test base switches every feature on unless a class sets `ALL_FEATURES = False`. Fixtures are invented data.
- Dates: parsing, formatting and sort keys for every qualifier, plus 29 February; year-less dates (accepted and rejected combinations, sorting, "age unknown", the living rule); time of birth and death.
- Relationships: a table of known kinships up to third cousins twice removed, half-relatives and in-laws; kin keys and terms (§13.1); sides (§13.16); relationship search (§13.17).
- Loop rejection, and undo/redo including conflicts (409); trash and purge.
- Media: magic-byte checks, SVG/HTML rejection, EXIF/GPS stripping, regions, photo fixes, inbox.
- Media storage: files land under `media_path` with fan-out; nothing is written when the folder is missing, unwritable or has a wrong marker (offline mode); first-start marker creation; a database-only backup restore reports missing media; `media_path` outside `/share/` is rejected.
- Export options: one fixture tree exported with each option switched off, checking that the detail is absent from the website HTML, `data.js` and the print data; living people never leak dates, places or photos under *limited*; scope filters (ancestors/descendants/branch cuts/selected); the website opens from `file://` with no network requests.
- Reminders: scope, leap years, lead days, dedupe, and never sending notes; the per-person switch (off by default, anniversaries when either partner is on, trashed people never, the close-family shortcut is one undoable batch, never exported); phones from Home Assistant; notify-service assignment is admin-only.
- Tithi and milestones against published panchangam dates; the quiz and kids mode; duplicates and merge; custom fields, sources and contacts/vCard; the places map against a fake Nominatim.
- App settings: admin-only, validation, applied at once, restore behaviour.
- Feature switches (`test_features.py`): new-install defaults, the existing-data rule (migration and restored backup), a stored value wins, the old map/inbox settings carry over, each module's UI data and API hidden while off (404 with `featureOff`), background jobs skip, exports leave switched-off data out, switching back on restores everything; the "No admin yet" flag and banner.
- Packaging (`test_packaging.py`): `config.yaml` (version, url, ingress, `panel_admin: false`, no ports, options = schema = translations, empty defaults), every `?v=` equals the version, README/DOCS present and free of release notes, a CHANGELOG.md with `# Changelog` and its newest (top) version heading equal to the config.yaml version, `spec/` present, icon 128×128 and logo 250×100, and a scan of every text file for personal details.
- Tree payload limits, and API permissions (disabled users 403, admin-only routes).

## 13. Modules
Everything stays in the one **SQLite** database plus media files. There's no separate database server.

### 13.1 Indian relationship names
- **What you see.** The household language is set by the **`relationship_language` App setting** (§3): English (the default), **Telugu** or **Hindi**. Each user can keep "App default" or pick another of the three in Settings.
  - Labels show the Indian term with the English meaning: "**Babai** — your father's younger brother".
  - The same label appears in the person header, the tree tooltip, search ("babai" finds everyone who is your Babai), and reminders ("🎂 Pinni Lakshmi turns 60 today").
- **Kin key.** `relations.py` turns the viewer → person path into a canonical key:
  - one letter per step: `F M B Z S D H W` (father, mother, brother, sister, son, daughter, husband, wife);
  - `+`/`-` for elder/younger where the language needs it;
  - optionally `@m`/`@f` for the *speaker's* gender.

  Examples: father's elder brother `F.B+`, mother's younger sister `M.Z-`, father's sister's husband `F.Z.H`, elder brother's wife `B+.W`, mother's brother's son `M.B.S`, and for a male speaker, sister's son `Z.S@m`.
- **Elder or younger** is decided by birth dates when both are known, otherwise by **birth order**. The order of children within a family is their birth order and can be dragged in the family editor. If neither is known, the key has no `+`/`-` and the neutral term is shown with a hint: "add a birth date or birth order to pick Peddananna or Babai".
- **Parallel vs cross cousins** come out of the key automatically: `F.B.*` and `M.Z.*` are parallel, `F.Z.*` and `M.B.*` are cross. That lets Telugu use sibling terms (Anna/Akka/Tammudu/Chelli) for parallel cousins and Bava/Maradalu for cross cousins.
- **Term table, editable by any user** (a Settings → Relationship names screen, with every change in history):
```sql
CREATE TABLE kin_terms (lang TEXT NOT NULL, kin_key TEXT NOT NULL,
  term TEXT NOT NULL, note TEXT,                      -- e.g. "also: Chinnanna"
  source TEXT NOT NULL DEFAULT 'seed' CHECK (source IN ('seed','custom')),
  updated_by TEXT, updated_at TEXT, PRIMARY KEY (lang, kin_key));
ALTER TABLE users ADD COLUMN kin_lang TEXT CHECK (kin_lang IN ('en','te','hi'));  -- NULL = App setting
```
  - **Seed rows** ship for about 60 common keys in `te` and `hi`, the ones from the design discussion: grandparents, parents' siblings and their spouses by elder/younger, siblings by elder/younger, siblings' spouses, in-laws, children-in-law, grandchildren, and parallel and cross cousins.
  - **Custom rows override seed rows,** so each family can use its own words, and "Reset to default" restores the seed.
- **Lookup** takes the most specific match: exact key with speaker gender, then exact key, then the key without the elder/younger mark. If nothing matches, the English label is shown.
  - The seed also covers the wider family (great-grandparents and great-grandchildren, grandparents' siblings and their partners, parents' cousins, second cousins, first cousins' partners and children, a partner's sibling's partner). A key with no term of its own is **composed**: the longest leading part that has a term, then the rest seen from that relative, with the speaker gender taken from that relative (`B+.W.B+` → te "Vadina Anna", hi "Bhabhi ka Bhaiya"; only the first of "A / B" alternatives and no bracketed notes are used). Only when a part has no term is the English label shown.
  - A key with one gender-neutral letter (P X C E: gender not set) that still has no term is tried with both genders and shown as "A / B" (`kin._either_gender`); the person page hints to set the gender. More rule-built seed keys: step-parents, children's parents-in-law, a partner's grandparents/uncles/aunts/nephews/nieces, a partner's cousins (by the partner's sibling words, `kin._derived`), siblings' children's partners, and siblings' and cousins' grandchildren.
  - `relations.relationship()` names relatives two marriages away when they're close (each blood part within 4 parent/child links) instead of "related by marriage": the partner of a blood relative of your partner ("wife's sister's husband", `W.Z-.H`) and a blood relative of the partner of your blood relative ("brother's wife's brother", `B+.W.B+`). Kind `inlaw`, with steps, so they get a kin term.
- **Tests:** the kin-key table for about 40 paths, elder/younger from dates vs birth order, speaker-gender terms, custom overrides, and the fallbacks.
- **Details:** - `relations.relationship()` also returns `steps` (the canonical walk: up to the generation below the common ancestor, one `sibling` step, then down; plus `spouse` steps for in-laws) and `gen`. `kin.build()` turns it into the key. Neutral letters `P X C E` (parent, sibling, child, spouse) stand in when a gender is unknown, so those keys fall back to English.
  - The elder/younger mark sits on each sibling step (compared with the person before it). A **cousin** (own generation, reached across a sibling) carries a second mark on the last step compared with the viewer: `F.B+.S-`. Lookup tries the exact key, then the key with only its **last** mark (`F.B.S-`, which is how cousin seeds are written), then none — each with `@m`/`@f` first.
  - Ages: birth dates compared to the precision both have; otherwise the order of children in a shared family.
  - Seeds live in `kin.py` (98 Telugu and 68 Hindi keys, including "neutral" rows such as `F.B` = "Peddananna / Babai" for unknown age); `kin_terms` holds only the family's own rows (`source = custom`), with `lang` + `kin_key` as a history entity, so edits and resets are batches with Undo. The term cache follows `tree_version`.
  - The request's language is set by `auth.require_user` (a context variable); background work (reminders) passes the user's language explicitly. Exports stay in English.
  - People search: when the text is a relationship word (English, or a term in the viewer's language), people whose label for the viewer contains that word match too.

### 13.2 Manual photo tagging
- **Tagging.** On any photo, *Tag people*: drag a box on the image and pick the person (search as you type). Several boxes per photo are allowed; tagging also links the photo to that person.
  - Hovering or tapping a box shows the name.
  - A person's Photos tab lists every photo they're tagged in.
- **Profile photos.** *Use as profile photo* on a box crops the photo to that box, and it becomes their photo. The crop is a region, so the original is untouched.
- **Schema:**
```sql
CREATE TABLE media_regions (id TEXT PRIMARY KEY, media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
  person_id TEXT REFERENCES people(id), x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,  -- 0–1 of width/height
  created_by TEXT, created_at TEXT NOT NULL);
ALTER TABLE people ADD COLUMN photo_region_id TEXT REFERENCES media_regions(id) ON DELETE SET NULL;
```
- No automatic face detection.
- **Details:** POST `/media/{id}/regions` `{personId, x, y, w, h}` (boxes are clamped into the photo; min 1 %; ≤ 100 per photo; also inserts the person's `media_links` row) · PATCH `/media/{id}/regions/{rid}` (move/resize, or another person — whose profile crop was that box loses it) · DELETE (the person stays tagged; a profile crop falls back to the whole photo) · PUT `/people/{id}/photo` `{mediaId, regionId?}` (the box must be this person's on that photo; without it the crop is cleared; uploading a new profile photo clears it too) · GET `/media/{id}/file?size=256|1024&region=` crops on the fly from the 1024 thumbnail: the box widened by 15 % to a square around its centre (a stale region id serves the whole photo). Media items carry `regions: [{id, personId, name, x, y, w, h, profile}]` (trashed people hidden); summaries carry `photoRegion`. Removing a person's tag hard-deletes their boxes in the same batch. `media_regions` is a history entity. The website export still uses the whole profile photo; GEDCOM `CROP` waits for GEDCOM.

### 13.3 Places map
- **Opt-in.** The App setting `enable_map` (default **false**, Admin → App settings) turns it on, because it needs internet: place names go to a geocoder, and map tiles are downloaded.
  - `map_tiles_url` defaults to `https://tile.openstreetmap.org/{z}/{x}/{y}.png`; the attribution is shown.
  - `nominatim_url` defaults to `https://nominatim.openstreetmap.org`.
  - The CSP is widened only for those two hosts when the map is on.
- **Geocoding.** Unique place strings from events are geocoded in the background with the **same approach as Household Todo's drive-time geocoder**: throttled to 1 request per second, one place per tick, retried with the unit stripped, and cached.
  - Anyone can correct a pin by dragging it, which pins it for that place string.
  - Only the place text is sent; no names or dates.
```sql
CREATE TABLE place_geo (place_key TEXT PRIMARY KEY,   -- normalized place text
  lat REAL, lon REAL, source TEXT NOT NULL CHECK (source IN ('geocoder','manual','failed')),
  checked_at TEXT NOT NULL);
```
- **Map view.** Vendored **Leaflet** (BSD-2, pinned by hash) shows markers for births, marriages, residences and deaths, coloured by type.
  - **Filters:** everyone, ancestors of X, descendants of X, or one person.
  - **Year slider:** show events up to year N.
  - **Migration lines** join each person's residences in date order, animated along the timeline.
  - Clicking a marker lists who was there, and when.
- Person pages gain a small map on the Timeline tab.
- **Details:** `geocode.py` (its own background loop: one lookup per second while there's work, else a check a minute; variants: as written, without a unit/flat/house number, then dropping leading parts down to two; `place_geo` also stores the display text, `tries` and `updated_by`; failed places are retried after 7 days, at most 3 tries, or at once with admin POST `/map/retry`). `routers/map.py`: GET `/map/status` → `{enabled, tilesUrl, attribution, places, located, failed, pending}` · GET `/map/points?filter=all|ancestors|descendants|person&personId=` → `{points: [{eventId, type, kind: birth|marriage|residence|death|other, year, sortKey, dateDisplay, place, placeKey, lat, lon, source, people}], unlocated: [{place, events, status}]}` (a family event's people are the partners; trashed people are left out) · PUT `/map/places` `{place, lat, lon}` (manual pin, by normalised place text, never overwritten by the geocoder) · DELETE `/map/places?place=` (look it up again). All 409 while the map is off. Leaflet 1.9.4 is vendored under `static/vendor/leaflet/` (hashes in its README, checked by tests) and loaded only when a map is shown. Markers are circle markers (no marker images) grouped by place; migration lines sit in their own pane under the pins. "Animated along the timeline" is the year slider with ▶ Play.

### 13.4 Duplicate finder & merge
- **Finder.** A background scan, plus *Find duplicates* in the People view, scores every plausible pair. It uses only pairs with the same first letter of surname or a matching phonetic key, to stay fast.
  - **Names:** similarity, including maiden names, nicknames and transliteration variants common in Indian names, e.g. Lakshmi/Laxmi, Venkat/Venkata, Srinivas/Shrinivas, and double letters and `th`/`t`, `v`/`w`, `sh`/`s`. These come from a small normalization table plus Jaro-Winkler similarity.
  - **Dates and places:** birth year ±2, birth place, and death year.
  - **Family:** matching parents' or partner's names.
  - Pairs above a threshold are listed with the reasons ("same parents, birth year 1950 vs 1951").
- **Merge screen.** Two columns side by side. For each fact you pick which value to keep, or keep both as alternate names or events.
  - Families, children, events, media, regions, stories, "This is me" links and history references move to the kept person.
  - The other person is soft-deleted with `merged_into`.
  - It's **one history batch**, so the whole merge can be undone.
- **"Not the same person"** dismissals are remembered.
```sql
ALTER TABLE people ADD COLUMN merged_into TEXT REFERENCES people(id);
CREATE TABLE not_duplicates (a TEXT NOT NULL, b TEXT NOT NULL, by_user TEXT, created_at TEXT NOT NULL,
  PRIMARY KEY (a, b), CHECK (a < b));
```

- **Details:** `duplicates.py` — names normalised for Indian spellings (ksh/x, aa/a, th/t, sh/s, v/w, doubled letters, a final *a*), Jaro-Winkler, blocking by surname initial and a 4-letter first-name key; birth/death years, birth place, gender, parents' and partners' names add or subtract; pairs ≥ 0.72, cached per tree version. `not_duplicates (a, b)`. Routes: GET `/duplicates`, POST `/duplicates/dismiss`, GET `/duplicates/compare?a=&b=`, POST `/people/{keep}/merge {otherId, choose}` (one batch; the other person is soft-deleted with `people.merged_into`). Known limit: a user's "This is me" moved by a merge isn't moved back by Undo. The GEDCOM merge mode waits for GEDCOM.

### 13.5 Printable wall chart & family book
- **Everything is generated in the browser** and printed or saved as PDF from the print dialog, so there's no server-side PDF library.
- **Wall chart.**
  - **Chart type:** ancestors, descendants or hourglass, from any person, with a chosen depth.
  - **Paper and layout:** A4–A1 or Letter–Tabloid, portrait or landscape, or **poster tiling** across several A4 sheets with cut marks and overlap.
  - **Printing:** uses the Daylight theme, with a title and a date line. It can also be downloaded as a standalone **SVG** for a print shop.
- **Family book.** Choose the people (a person with N generations of ancestors and/or descendants, or everyone). It produces:
  - a title page;
  - a table of contents;
  - one section per person: photo, names, key facts, a timeline, parents, partners and children with page references, and stories;
  - a photo gallery appendix;
  - an index of names.
- **Privacy and details:** chosen in the Export options dialog (§13.6.1), the same as every other export.
- **Details:** `static/print.js`, routes `#/print/chart` and `#/print/book`. POST `/export/print {options}` returns the filtered `export_view` projection as JSON (no file paths); the pages offer the living/dates/photos/places choices and take the rest from the user's last website export. Chart: tidy tree layout, SVG with gender stripe and optional photo; paper A4–A1, Letter, Tabloid, or A4 poster tiles (1 cm overlap, dashed cut lines, shaded overlap, row/sheet label); SVG download with photos embedded as data URIs. Book: numbered sections (§n) instead of page numbers, which the browser can't know; contents, gallery (≤ 300 photos), index.

### 13.6 Export options & website export
#### 13.6.1 Export options dialog (used by every export)
One dialog, opened by every Export button. Each format shows only the options that apply to it.

| Group | Choices (default in **bold**) |
|---|---|
| **Who** | **Whole tree** · ancestors of X (N generations) · descendants of X (N generations) · ancestors + descendants of X · **a branch with stop points** (below) · selected people (multi-select from the People list). "Also include partners of included people" (**on**). |
| **Living people** | People marked *keep out of exports* (§13.20) are always left out first. Then: **Limited** (name and relationships only) · full · exclude. *Limited* always wins over the detail choices below, so a living person's dates, places, photos and notes never go out unless *full* is picked. |
| **Quick levels** | **Names only** · **Standard** (the defaults below) · **Everything**, which set every detail row at once; the rows can then be changed one by one. The person's name and the family links (who is whose parent, partner, child) are the only things that can't be switched off. *Names only* gives a site with names and links and nothing else: no photos, dates, places, events, stories, gender or relationship labels. |
| **About each person** | Gender (**on**; off: everyone neutral in colours and labels, "parent" not "mother"). Who has died (**on**; off: no 🕯, no death or burial details, years shown as "b. 1920"). Family details (**on**; off: no married/partners, divorced/separated, adopted/step/foster). Relationship labels are worked out on a copy of the tree without the switched-off details, so a label can't give them away. |
| **Names** | Maiden/married names (**on**), nicknames and alternate names (**on**). |
| **Dates** | **Full dates** · year only · none. |
| **Places** | **Include** · leave out. |
| **Events** | One checkbox per group, all **on** except the last: births, deaths & burials · marriages, engagements & divorces · education & occupation · residences · immigration & emigration · military · religious & ceremonies (§13.14) · other events. |
| **Photos & documents** | Photos: none · profile photo only · **all photos**. Size: **web size (1024 px)** · originals. Photo tags (§13.2) (**on**). Documents (**off**). |
| **Text** | Biography and stories (**on**). Notes (**off**, since notes are often research scraps). |
| **Extra details** | Each custom field (its own default, §13.8) · sources (**off**) · **contact details (always off by default, §13.11)**. Only while the module's switch is on (§3.1). |
| **Relationship labels** | (website and book) Off · **relative to a person** (defaults to your "This is me"), in English: "Lakshmi — Arjun's aunt". |

##### Branch with stop points ("start at my father, stop at my wife")
For exporting one part of the family: start from one or more people, walk outwards, and **cut** wherever you don't want the walk to continue. There can be any number of cuts.

- **Start from** one or more people, for example *Venkat (my father)*.
- **Walk:** *descendants* · *ancestors* · *both* (ancestors and descendants, no siblings) · **blood relatives** (`connected`: ancestors, everyone descended from them — uncles, cousins — and the start people's own descendants).
- **Married-in partners:** **include them but stop there** (the default) · follow their families too · leave them out. With the default, spouses appear but their parents and siblings don't, which already covers most "don't bring in my wife's family" cases in one click.
- **Cuts**, each on a person, as many as needed:
  - ✂ **Stop here:** include this person but don't go past them. *Stop at Anjali* keeps Anjali and not her parents or brother.
  - ⛔ **Leave out:** skip this person and everything that's only reachable through them. *Leave out Ravi* drops Ravi and his branch.
  - A cut can be switched to *leave out, but keep the chain*: the person becomes a "Private" placeholder (as in §13.20) so the people beyond them still connect.
- **Choosing cuts on a picture, not a list.** The dialog shows the Everyone chart (§11) of the walk: included people in full colour and everyone left out greyed. Tapping any card opens a small menu: *Start here*, *Stop here*, *Leave out*, *Clear*. Cut people carry a ✂ or ⛔ badge, and the live count updates as you go ("58 people · 3 cuts"). A plain list of the cuts with ✕ buttons sits under the chart for phones.
- **Save it as a preset** ("Venkat's family without in-laws") like any other export choice, so next year's export is one click. Presets store person ids, so they keep working after renames; a cut on a deleted person is dropped with a warning.
- **How the walk works** (server, `export_view.branch_walk`): a breadth-first walk from the start people. Each step carries a direction: going **up** to parents keeps the walk's upward mode (for *connected*, up and down again, which reaches siblings and cousins); going **down** to children is always down-only, so a child never leads back up to its other parent's family. Partners are married-in: with *stop* they're added but not expanded; with *follow* they're walked like a start person (so *follow* on *connected* reaches every connected person, and cuts decide where it ends). A *stop* person is added but not expanded, and a *leave-out* person is never entered. Someone reachable by another route without passing a cut is still included (the cut blocks a path, not the whole family). Then the living rule and the detail choices apply as usual.
- **API:** `options.scope = {type: "branch", start: [ids], walk: "descendants|ancestors|both|connected", partners: "stop|follow|exclude", stops: [ids], leaveOut: [{id, keepChain}]}`; `/export/preview` also returns the included ids so the chart can grey out the rest.
- **The same scope works everywhere** the Export options dialog is used: website, family book and wall chart.
- **Tests:** a father-rooted walk with a stop at the wife excludes her parents and siblings but keeps her and the children; several stops and leave-outs together; a person reachable round a cut is kept; the "Private" placeholder keeps the chain; presets survive renames and drop deleted cuts.

- **Preview before exporting.** The dialog shows a live summary from `/export/preview`: "412 people (38 living, limited) · 1 204 photos · about 180 MB", plus warnings such as "Photos off: profile pictures will show initials".
- **Presets.** The last choices are remembered per user and per format. You can also save named presets ("For cousins", "Full archive"), visible to everyone:
```sql
CREATE TABLE export_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
  format TEXT, options TEXT NOT NULL,          -- JSON, the dialog's choices
  created_by TEXT, updated_at TEXT NOT NULL);
ALTER TABLE users ADD COLUMN export_last TEXT;  -- JSON {format: options}
```
- **Enforced on the server, in one place.** Every exporter reads from one filtered projection, `export_view(options)`, which applies scope, the living rule and the detail choices. No format filters on its own, so a switched-off detail can't leak through one format.
- **Jobs.** Large exports run as a background job with progress (one at a time per user). The file is written to `<media_path>/.exports/`, not `/data`, so it never ends up in an HA backup. It is deleted after 1 hour.
- **History.** Each export is logged in History ("Arjun exported the website: 412 people, living limited, photos all"), with no undo.

#### 13.6.2 Website export — the family home page
- **What you get:** `family-tree-site-YYYYmmdd.zip`. Unzip it and double-click `index.html`. It works **offline from `file://`** in any browser, and can also be copied to a USB stick, a NAS share, or any static web host.
- **Built on the server** (`site_export.py`) from `export_view(options)`, with the standard library only (`html.escape`, templates as Python strings, `zipfile`). No new dependencies.
- **Pages:**
  - **`index.html` — the home page:** the site title and an intro paragraph (both typed in the dialog), a cover photo, the home person's card with "Open the tree", a search box, stats (people, generations, surnames, earliest year), and links to the other pages.
  - **`tree.html`:** the same interactive hourglass chart as the app (pan, zoom, tap to re-centre), using the app's own `tree.js` (copied into `assets/`), starting from the home person.
  - **`people/<name>-<shortid>.html`:** one page per person with the photo, names, dates, relationship label, parents, partners and children (all linked), a timeline, stories and a gallery, in each case only what the options allow.
  - **`people.html`** (A–Z with search), **`surnames.html`**, and optionally **`places.html`** (a list of places with who was born, married, lived or died there; no online map, so it stays offline).
- **Assets:** `assets/site.css`, `assets/site.js`, `assets/tree.js`, and `assets/data.js`, which holds the tree as `window.TREE = {…}`. It is a script rather than JSON because browsers block `fetch()` from `file://`. Media go in `media/` at the size chosen.
- **Website extras in the dialog:** title, intro text, home person (defaults to "me"), cover photo, theme (Heritage, Slate, Daylight, Parchment or Auto; visitors can switch too), and which pages to include.
- **Safety:**
  - No external requests: no fonts, CDNs, analytics or map tiles. Every page carries a strict CSP `<meta>` tag, and all text is HTML-escaped.
  - There's **no password protection**, since a static site can't enforce one. Anyone with the files can read everything in them. The dialog says so, and makes you confirm again when *Living people: full* is combined with the website format.
  - EXIF/GPS is already stripped from images on upload (§4), so exported photos carry no location.
- **Size:** 20 000 people means about 20 000 small pages, and `data.js` is about 5 MB. The tree page loads people progressively so it stays usable.

### 13.7 Names in Telugu / Hindi script
- **Fields.** Each person gets an optional given name and surname in script ("లక్ష్మి", "शर्मा"), next to the English spelling. The script is detected from the characters (Telugu U+0C00–0C7F, Devanagari U+0900–097F).
- **Entry.** Type the name with a phone's Telugu or Hindi keyboard, or use **Suggest from English**, which runs a vendored MIT-licensed transliteration library (Sanscript) and fills in an editable guess.
- **Display.** A per-user setting chooses *English*, *Script* or *Both*. With *Both*, the script name appears under the English one on cards and headers.
- **Name order.** The App setting `name_order` (`given_first` by default, or `surname_first`, the Telugu convention "Sharma Arjun") sets the household default, and any person can override it.
- **Search** matches either spelling.
```sql
ALTER TABLE people ADD COLUMN given_local TEXT;
ALTER TABLE people ADD COLUMN surname_local TEXT;
ALTER TABLE people ADD COLUMN name_order TEXT CHECK (name_order IN ('given_first','surname_first'));  -- NULL = the name_order App setting
```
- **Details:** `people.given_local`, `surname_local`, `name_order`; App setting `name_order`; `users.name_display` (`en|script|both`, PUT `/me/name-display`); `names.py`. Sanscript (vendored under `static/vendor/sanscript/`) for *Suggest from English*. Search matches script names.

### 13.8 Custom fields
- **Admins define the fields** (Settings → Custom fields); **everyone fills them in.** Each field has a label, a kind (text, choice, place or date), whether it applies to people or families, an order, whether it shows on tree cards, and whether exports include it by default.
- **Starter templates** that an admin can add with one tap (nothing is created automatically): Native village (place), Gotram (text), Nakshatram (a choice of 27), Rasi (a choice of 12), and Kuladaivam / family deity (text).
- They appear in a **Details** block on the person page, as a **People-list filter** ("Native village = Kakinada"), and in search.
- Removing a field **archives** it: the values are hidden but kept, and it can be restored. Every value change is recorded in history.
- **Export:** each field is its own checkbox in the Export options dialog (§13.6.1).
```sql
CREATE TABLE custom_fields (id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('text','choice','place','date')), choices TEXT,   -- JSON list for 'choice'
  applies_to TEXT NOT NULL DEFAULT 'person' CHECK (applies_to IN ('person','family')),
  position INTEGER NOT NULL DEFAULT 0, on_card INTEGER NOT NULL DEFAULT 0,
  export_default INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE custom_values (id TEXT PRIMARY KEY, field_id TEXT NOT NULL REFERENCES custom_fields(id),
  person_id TEXT REFERENCES people(id) ON DELETE CASCADE, family_id TEXT REFERENCES families(id) ON DELETE CASCADE,
  value TEXT NOT NULL, updated_by TEXT, updated_at TEXT NOT NULL,
  CHECK ((person_id IS NULL) <> (family_id IS NULL)));
```
- **Details:** `custom_fields`, `custom_values` (history entity). Admin: GET/POST `/custom-fields`, POST `/custom-fields/templates/{native_village|gotram|nakshatram|rasi|kuladaivam}`, PATCH (label, choices, archived, onCard, exportDefault, position). Everyone: PUT `/people/{id}/custom`, PUT `/families/{id}/custom` (one batch each), GET `/custom-fields/{id}/values`. People list: `field`/`value` filter, search includes values. Export: `options.customFields {fieldId: bool}`.

### 13.9 Sources
- **A source** records where information came from: a certificate, a document, a book, a website, a photo, or an **interview** ("told by Grandma, 2026"). An interview links to the person who told it. A source can point to a scanned document already in Media.
- **Citations** attach a source to a person, family, event or single fact, with a page or detail, a confidence level (0–3, as GEDCOM's `QUAY`) and a note.
- **In the app:** every fact has a small 📎 count. Tapping it lists the citations, with *Add source*. A **Sources** view lists every source and what it supports.
- **Export options:** a *Sources* checkbox (off by default).
```sql
CREATE TABLE sources (id TEXT PRIMARY KEY, title TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('certificate','document','book','website','photo','interview','other')),
  author TEXT, date_text TEXT, repository TEXT, url TEXT, note TEXT,
  media_id TEXT REFERENCES media(id), told_by_person_id TEXT REFERENCES people(id),
  gedcom_id TEXT, created_by TEXT, created_at TEXT NOT NULL, deleted_at TEXT);
CREATE TABLE citations (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
  person_id TEXT REFERENCES people(id), family_id TEXT REFERENCES families(id), event_id TEXT REFERENCES events(id),
  fact TEXT,                -- e.g. 'name', 'gender', 'custom:<field_id>'; NULL = the whole record
  page TEXT, quality INTEGER CHECK (quality BETWEEN 0 AND 3), note TEXT, created_at TEXT NOT NULL,
  CHECK ((person_id IS NOT NULL) + (family_id IS NOT NULL) + (event_id IS NOT NULL) = 1));
```
- **Details:** `sources` (soft delete) and `citations` (exactly one of person/family/event; fact, page, quality 0–3), both history entities. Routes: `/sources` CRUD, `/citations` POST/PATCH/DELETE/GET. UI: 📎 buttons, More → Sources. Export: `options.sources`.

### 13.10 Inbox folder import
- **How it works.** Drop photos or PDFs into **`<media_path>/inbox/`**, from a PC through the Samba share, from the NAS, or from a phone's file app. The App setting `inbox_enabled` is on by default.
- **Scan.** Every 2 minutes (and on *Check now*), a file is picked up once its size has stayed the same for 30 seconds, so half-copied files are left alone. Each file goes through the normal upload pipeline (§4): magic-byte check, re-encoding, reading the date taken, then stripping EXIF.
  - **Duplicates** are skipped using the SHA-256 of the original file (`orig_sha256`), so dropping the same scans twice does nothing.
  - **Success:** the file is moved into media storage and removed from the inbox.
  - **Rejected** files (wrong type, too big) go to `inbox/_rejected/` with a `.txt` note explaining why.
  - **Subfolder names** become the title prefix, and are matched against people's names as a *suggested* person: `inbox/Wedding 1985/` or `inbox/Venkat/`.
- **Unsorted queue** (Media → *Unsorted*, with a count badge): a grid with multi-select for bulk actions. Tag people (drag a box, §13.2), set the date and title, link to an event, or delete. *Done* removes items from the queue.
- **History:** each scan is one batch, recorded as "Inbox". The scan pauses while media is offline (§5.1).
```sql
ALTER TABLE media ADD COLUMN unsorted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE media ADD COLUMN orig_name TEXT;
ALTER TABLE media ADD COLUMN orig_sha256 TEXT;
CREATE INDEX idx_media_orig ON media(orig_sha256);
```
- **Details:** `inbox.py` scans `<photo folder>/inbox` every 120 s (files unchanged for 30 s), one batch "Inbox: N files" with no user; `media.unsorted`, `orig_name`, `orig_sha256` (duplicates by hash). Rejects go to `inbox/_rejected/` with a `.txt` note. Routes: POST `/inbox/scan`, GET `/inbox/status`, GET `/media?unsorted=true`, POST `/media/bulk-link`, POST `/media/sorted`. App setting `inbox_enabled`.

### 13.11 Contact details for living relatives
- **What you can store:** phone, WhatsApp, email, postal address and "other", each with a label ("home", "India mobile"). Contact details are for **living people only**. Once someone is marked deceased, their contacts are hidden, though still kept in history.
- **Person page:** a *Contact* block with buttons: 📞 `tel:`, WhatsApp `https://wa.me/<digits>`, `mailto:`, and an address that opens in the phone's maps app.
- **Phone numbers** are stored in international form (+91…, +1…). The App setting `default_phone_code` (default `+1`) is used when a number is typed without a code, and the field shows the result before saving.
- **Address book view:** living relatives with contact details, filterable by *close family* / relationship / surname. **Download vCard (.vcf)** for the selection imports into a phone's contacts. The vCard includes the English and script names, contacts, and a birthday; a year-less birthday uses the iPhone "omit year" form.
- **Privacy:** visible to every enabled household user, like the rest of the tree. **Never exported by default.** *Contact details* is a separate checkbox in the Export options dialog, off by default, and the website format asks you to confirm it a second time.
```sql
CREATE TABLE contacts (id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('phone','whatsapp','email','address','other')),
  label TEXT, value TEXT NOT NULL, position INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
```
- **Details:** `contacts` (living people only; phone/WhatsApp normalised to E.164 with App setting `default_phone_code`). Routes: POST `/people/{id}/contacts`, PATCH/DELETE `/contacts/{id}`, GET `/phone-preview`, GET `/contacts`, GET `/contacts.vcf?ids=` (BDAY without a year uses the `--MMDD` form). Export: `options.contacts`, off by default, website export asks a second time.

### 13.12 Tithi death anniversaries — shraddha
- **What it does.** A death event can carry a **tithi**: the lunar month (Chaitra … Phalguna), paksha (Shukla/Bahula) and tithi (Padyami … Pournami/Amavasya). Each year the app finds the calendar date and sends a reminder: "🪔 Tithi for Grandpa Venkat — Ashwayuja Bahula Dashami — is on Tue 14 Oct (in 7 days)".
- **Entering it:**
  - **Tithi known, date not** (common for older generations): pick the month, paksha and tithi directly.
  - **Death date known:** the app computes the tithi for that day. If the tithi changed during that day, it asks which one applies (before or after hh:mm).
- **Calculation, offline, with no new dependencies** (`panchang.py`):
  - Sun and Moon positions from Meeus' algorithms (the Moon to about 10″), giving tithi = ⌊((λMoon − λSun) mod 360°) / 12°⌋ + 1.
  - **Months are amanta**, as in Telugu calendars: a month runs from new moon to new moon and is named after the Sun's sidereal sign at its starting new moon (Lahiri ayanamsa): Meena → Chaitra, Mesha → Vaishakha, and so on. A month with no solar sign change is **adhika** (leap), and the shraddha falls in the regular (*nija*) month.
  - **Observance day:** the App setting `tithi_rule` is `aparahna` by default (the day when the tithi covers the afternoon period, the usual shraddha rule), or `sunrise`.
  - **Location and timezone** come from HA's home settings (`/api/config`), because the date can differ between India and the US.
- **Overrides.** Any year's date can be corrected by hand ("our priest says the 15th"), and the correction is kept for that year. The first time, the app suggests checking its dates against your family's panchangam.
- **Reminders:** a *Tithi* switch (on by default) and `tithi_lead_days` (default 7, range 0–30) in each user's reminder preferences, plus a same-day reminder. The Upcoming view shows tithis with their names in the relationship language (Telugu, Hindi or English).
- The same works optionally for **birth tithi** (a *janma tithi* birthday) on a birth event.
- **Tests:** fixtures of published Telugu panchangam dates across several years, including an adhika-masa year (2026, Adhika Jyeshtha); both observance rules; a death date with a tithi change during the day; and overrides.
```sql
CREATE TABLE event_tithi (event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  masa INTEGER NOT NULL CHECK (masa BETWEEN 1 AND 12),          -- 1 = Chaitra
  paksha TEXT NOT NULL CHECK (paksha IN ('shukla','krishna')),
  tithi INTEGER NOT NULL CHECK (tithi BETWEEN 1 AND 15),        -- 15 = Pournami / Amavasya
  source TEXT NOT NULL CHECK (source IN ('entered','computed')));
CREATE TABLE tithi_dates (event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE, year INTEGER NOT NULL,
  date TEXT NOT NULL, overridden_by TEXT, computed_at TEXT NOT NULL, PRIMARY KEY (event_id, year));
ALTER TABLE reminder_prefs ADD COLUMN tithi INTEGER NOT NULL DEFAULT 1;
ALTER TABLE reminder_prefs ADD COLUMN tithi_lead_days INTEGER NOT NULL DEFAULT 7 CHECK (tithi_lead_days BETWEEN 0 AND 30);
```
- **Details:** `panchang.py` (Meeus Sun ch. 25 and Moon ch. 47, Lahiri ayanamsa, amanta months named by the sidereal sign at the new moon, adhika when there's no sign change, sunrise equation; aparahna = 4th fifth of daytime, greatest overlap) checked against Ugadi 2023–26, Mahalaya Amavasya 2024/2025, Adhika Shravana 2023 and Adhika Jyeshtha 2026. `tithi.py`: `event_tithi` (history entity), `tithi_dates` cache (computed rows dropped when the rule, location, time zone or any tithi changes; hand corrections are history entities). Location from HA `/config` (Hyderabad until known). Routes: GET `/events/{id}/tithi/options`, PUT/DELETE `/events/{id}/tithi`, PUT/DELETE `/tithi/{eventId}/{year}`, GET `/tithi/dates?year=`. Birth events can carry a janma tithi. Upcoming kind `tithi`; reminder prefs `tithi`, `tithi_lead_days` (0–30, default 7).

### 13.13 "Who is this?" photo quiz
- **For kids and everyone else,** from the Tree menu. It uses profile photos and tagged photo regions (§13.2); people without a photo are skipped.
- **Modes:**
  - **Who is this?** A photo with 3–4 name choices.
  - **What do you call them?** A photo with choices of relationship terms in the relationship language: "Pinni", "Atta", "Peddamma". This is the best way for kids to learn the Telugu or Hindi words.
  - **Find them:** a name with 4 photos to pick from.
- **Play as** any person in the tree, which defaults to your "This is me". Children often don't have an HA login, so a parent picks the child's person, and relationship terms are worked out from the child's point of view.
- **Settings:** scope (close family or everyone), including the deceased (on/off), and difficulty. Wrong choices come from the same generation and gender, so the answer isn't obvious.
- **Learning:** people who are often missed come up more often (simple spaced repetition). After each answer the correct name and relationship are shown ("Lakshmi — your Pinni, Amma's younger sister"). Large tap targets, and no timer.
- Stats are kept per player, so each child has their own progress:
```sql
CREATE TABLE quiz_stats (player_person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE, mode TEXT NOT NULL,
  correct INTEGER NOT NULL DEFAULT 0, wrong INTEGER NOT NULL DEFAULT 0, last_seen TEXT,
  PRIMARY KEY (player_person_id, person_id, mode));
```

#### Kids mode
- **Why.** "Play as" runs inside a parent's HA session, so without a lock a child could leave the quiz and edit the tree under the parent's name. Kids mode shows the quiz and nothing else until a grown-up unlocks it.
- **Starting it.** In the quiz setup, a parent picks *Play as*, the modes and scope, and optionally a **time limit** (off, 10, 15, 30 or 60 minutes), then taps **Start kids mode**.
- **While locked:**
  - Only the quiz screen is shown: no sidebar, menus, person pages or links. The child can switch between quiz modes, but "Play as" is fixed.
  - The quiz goes full-screen where the browser allows it; otherwise it fills the app's panel.
  - **Enforced on the server, not just hidden:** the app sends a per-device id (`X-Device-Id`, a random value in `localStorage`). While that device is in kids mode, every route except the quiz and its photos returns **423 Locked**. Reloading the page brings kids mode straight back.
  - When the time limit runs out: "Time's up! Ask a grown-up". The quiz stops until someone unlocks.
- **Unlocking.** Hold the small 🔒 corner button for **3 seconds**, then:
  - enter the parent's **kids-mode PIN** (4–6 digits, set in Settings, stored as a PBKDF2 hash), or
  - if no PIN is set, answer a **grown-up question** made by the server (for example "47 + 38"), which is enough for young children.
  - **5 wrong tries** lock unlocking for 5 minutes. Any admin can also end kids mode for a device from Admin → Users.
- **What it doesn't lock:** HA's own sidebar and other panels outside the app, and the phone itself. For a full lock, use the device's own feature: **iOS Guided Access** or **Android screen pinning**. DOCS.md explains how, and a hint appears the first time kids mode starts.
- Quiz answers aren't tree changes, so nothing appears in History.
- **Tests:** writes and non-quiz reads return 423 while locked; locked state survives a reload; PIN and question unlocks work; the wrong-try lockout; the time limit; an admin force-unlock; and other devices of the same user are unaffected.
```sql
ALTER TABLE users ADD COLUMN kid_pin_hash TEXT;            -- NULL = use the grown-up question
CREATE TABLE kid_sessions (device_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  player_person_id TEXT NOT NULL REFERENCES people(id), modes TEXT NOT NULL,   -- JSON
  started_at TEXT NOT NULL, ends_at TEXT,                   -- NULL = no time limit
  failed_unlocks INTEGER NOT NULL DEFAULT 0, unlock_blocked_until TEXT);
```
- **Details:** `routers/quiz.py`, `kidmode.py`. POST `/quiz/next {playerId, mode, scope, deceased, difficulty}` → a question with a one-time token (kept in memory for an hour); POST `/quiz/answer {token, choice}` → correct, answer, explanation, running score; GET `/quiz/stats`. Weights: unseen 2, else (1 + 2·wrong)/(1 + correct), the last 5 targets ×0.05. Distractors: same gender first, then nearest generation (easy: 3 choices, looser). Kids mode: middleware returns 423 for every `/api` route except GET `/kidmode`, POST `/kidmode/challenge|unlock`, POST `/quiz/next|answer`, GET `/media/{id}/file`; quiz routes also 423 once the time limit passes. POST `/kidmode/start`, PUT `/me/kid-pin` (PBKDF2-SHA256, 200 000 rounds), GET `/admin/kidmode`, DELETE `/kidmode/{deviceId}` (admin). `kid_sessions.challenge` holds the sum's answer.

### 13.14 Indian ceremonies as event types
- **Built-in ceremony types**, shown in the user's relationship language:
  - Person: Barasala / Namakaranam (naming), Annaprasana, Aksharabhyasam, Upanayanam, Seemantham, Shashtipoorthi, Sahasra Chandra Darshanam.
  - Family: Nischitartham (engagement), Gruhapravesham (housewarming).
  - Plus *Other ceremony* with a free title.
- They appear on the timeline and person page like any event, with date, place, photos and sources, under the Export options group **religious & ceremonies**.
- Labels live in code as `{type: {en, te, hi}}`, e.g. `upanayanam: {en: "Sacred thread ceremony", te: "ఉపనయనం", hi: "उपनयन"}`.
- **Details:** `ceremonies.py` with English, Telugu and Hindi labels; person types namakaranam, annaprasana, aksharabhyasam, upanayanam, seemantham, shashtipoorthi, sahasra_chandra, ceremony; family types nischitartham, gruhapravesham, ceremony. Exported in the *religious* event group.

### 13.15 Milestone birthday & anniversary alerts
- **Milestones are marked** in reminders and the Upcoming view with 🎉 and **extra notice**: at 90, 30 and 7 days before, and on the day. For example, "🎉 Nanna's Shashtipoorthi (60) is in 3 months — Sat 14 Mar".
- **Default list**, editable by an admin in Settings → Milestones:
  - age 1 (first birthday), 60 (Shashtipoorthi), 70, 80;
  - **Sahasra Chandra Darshanam**: the day of the person's **1000th full moon** after birth (about 80 years 8 months), computed with `panchang.py` (§13.12);
  - wedding anniversaries 25 (silver), 50 (golden) and 60 (diamond).
- **Only for the living, and only when the birth or wedding year is known** (the age has to be calculable). If the person has a birth tithi (§13.12), the tithi date is shown too, because many families hold the ceremony on it.
- **A user switch:** *Milestones* (on by default) in reminder preferences.
```sql
CREATE TABLE milestones (id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('age','anniversary','full_moons')), value INTEGER NOT NULL,
  label_en TEXT NOT NULL, label_te TEXT, label_hi TEXT,
  lead_days TEXT NOT NULL DEFAULT '[90,30,7,0]', enabled INTEGER NOT NULL DEFAULT 1);
ALTER TABLE reminder_prefs ADD COLUMN milestones INTEGER NOT NULL DEFAULT 1;
```
- **Details:** `milestones` table seeded with the defaults (notice days 90, 30, 7, 0); `milestones.py`; GET `/milestones`, admin POST/PATCH/DELETE. Birthday and anniversary entries that hit a milestone carry `milestone` (🎉 chip); Sahasra Chandra Darshanam is its own entry. Reminder pref `milestones` (default on); lines like "🎉 Ravi — Shashtipoorthi, turns 60 on Thu 15 Oct (in 4 weeks)".

### 13.16 Father's side / mother's side colours
- **Tree toggle "Colour by side",** worked out relative to the viewer's "me" (or the focus person):
  - **paternal:** reached through your father;
  - **maternal:** reached through your mother;
  - **both:** reached both ways, which happens after cousin (menarikam) marriages; shown striped;
  - your own line: uncoloured.
  - Relatives who aren't ancestors get the side of their closest common ancestor with you, and partners take the side of the person they married.
- **Filter "Show: Both sides / Father's side / Mother's side"** hides the other side, which keeps big trees readable. The People list gets the same filter.
- Colours are theme variables (`--side-paternal`, `--side-maternal`), a colour-blind-safe pair, with a legend.
- **Details:** `sides.py`. The father and mother come from the birth family (a parent of unknown gender takes the free slot). Non-ancestors take the sides of their nearest common ancestors with the anchor; when those are both parents (a full sibling or their line) the person is "own line" (no side). Partners of a sided person take that side. Cached per tree version and anchor. On the chart, "Show: Father's side / Mother's side" **fades** the other side (opacity) rather than removing cards, so the layout doesn't jump; the own line never fades. Both views remember the choice per browser.

### 13.17 Relationship search
- **A picker, not free AI:** [Relationship ▾] of [Person ▾], defaulting to "me", plus filters (living, side, generation). Examples:
  - "first cousins of me";
  - "everyone on Amma's side";
  - "all my Babais";
  - "descendants of Venkat";
  - "in-laws of Ravi".
- **Typing works too.** The search box recognises phrases like these, including Telugu and Hindi terms from `kin_terms` ("babai" → key `F.B-`), and fills in the picker.
- **How it works:** one breadth-first walk from the anchor person gives a kin key and a label for everyone within 6 steps. It's cached per anchor and cleared on any family change.
- **Results** come as a list with relationship labels. Any result list can be used directly as the **"Selected people"** scope in the Export options dialog, the Address book (§13.11) and the quiz scope (§13.13).
- **Details:** `related.py`, `routers/related.py` (registered before `people.py`). Categories: relatives, blood, parents, grandparents, ancestors, siblings, uncles_aunts, first_cousins, cousins, nieces_nephews, children, grandchildren, descendants, partners, in_laws; plus `term` (a kin word matched against the label or the English label), `gender`, `side` (relative to the anchor), `living` and `generation` (−10…10). Everyone within 6 steps (parent, child, sibling, partner) is named once per tree version, anchor and language. The parser understands "X of Y", "Y's X", "my X", "on <father|mother|Nanna|Amma|…>'s side", singular and plural, and kin words in any language. UI: People → **Relatives** (typed phrase + picker + filters), and **Export these people** puts the ids into the Export draft as *Pick people*.

### 13.18 Quick family entry
- **"Add family" form:** partner 1 and partner 2 (each an existing person or a new one: name, gender, birth, and death if applicable), the marriage date and place, and **child rows** (name, gender, birth date, relation type). Drag the rows to set birth order, and add more with *+ child*.
- **Variant from any person:** *Add their parents & siblings* fills in the generation above in one go.
- **Fast to use:** Tab and Enter move through the fields. On phones the form goes step by step.
- **Avoiding duplicates:** as you type a name, likely existing matches are suggested (a light version of §13.4's scorer), so you link to someone instead of creating a duplicate.
- **Saving:** everything is one history batch, so one Undo removes the whole family.
- **API:** POST `/families/quick` `{partners:[…], marriage:{…}, children:[…]}`.

### 13.19 Photo fixes for scans
- **Tools:** rotate by 90°, **straighten** (a ±15° slider with a grid overlay), crop, and *Auto contrast* (Pillow `ImageOps.autocontrast`), for faded scans.
- **Non-destructive:** the original file is never changed. The edits are saved as a recipe on the media row, and the server renders a `display` file and thumbnails from original + recipe. *Revert to original* clears the recipe. Each edit is in history and can be undone.
- **Tags keep working:** photo-tag regions (§13.2) are stored against the original and transformed for display, so rotating or cropping never moves a tag onto the wrong face.
- **Exports** (website, book) use the edited version.
```sql
ALTER TABLE media ADD COLUMN edit TEXT;   -- JSON {rotate, angle, crop:{x,y,w,h}, autocontrast}; NULL = as uploaded
```
- **Details:** `photoedit.py` recipe `{rotate 0/90/180/270, angle ±20, crop, autocontrast}` in `media.edit` (history); edited display/thumbnail files cached by recipe key; region boxes stay stored on the original and are converted for display (`visible` false when cropped away). PUT `/media/{id}/edit`, GET `/media/{id}/preview?edit=`, `size=display`.

### 13.20 "Keep out of all exports" flag
- **A checkbox on the person page.** Any user can set it, and the change is in history. The person is then **left out of every export**: website, book, wall chart and vCard. No choice in the Export options dialog can override it; only unticking it on the person can.
- **Keeping the tree connected:** if leaving someone out would break a chain (for example, they are the parent linking two generations), the export puts in a placeholder person named **"Private"** with no details. Otherwise they're simply left out.
- **Visible in the app:** a 🚫 *Kept out of exports* chip on their page, and the export preview says "3 people are marked *keep out of exports*".
- Enforced in `export_view` (§13.6.1), so every format follows it.
```sql
ALTER TABLE people ADD COLUMN never_export INTEGER NOT NULL DEFAULT 0;
```

## 14. Possible future work
- **GEDCOM import and export** (5.5.1 `.ged` in UTF-8/UTF-16/ANSEL, 7.0 `.ged`/`.gdz` with media) through the same Export options, with a preview step, *empty* / *append* / *merge* (matched with the duplicate scorer, §13.4) import modes as one undoable batch, and mappings for the modules: photo regions as `OBJE`/`CROP`, script names as `NAME`/`TRAN` (7.0) or an extra `NAME` with `_LANG` (5.5.1), custom fields as `FACT`/`TYPE`, sources as `SOUR`/`PAGE`/`QUAY`, tithis as a `_TITHI` extension tag, ceremonies as `EVEN`/`TYPE`, and year-less dates as date phrases. The `gedcom_id` / `gedcom_extra` columns are reserved for it.
- **HEIC uploads** converted to JPEG on the server.
