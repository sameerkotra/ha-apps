# Household Chat — design spec

The developer reference for the app: what it does and how it's built. It describes the app as it is now. The user guide is `DOCS.md`. Section numbers are cited from code comments.

**Scale.** Built for **4–6 people**. The limits below are generous safety caps, not targets, and the design favours simple over scalable: one process, SQLite, and Server-Sent Events held in memory. Conventions shared with the sibling household apps (ingress identity, admins, themes, App settings, backups, notify targets) are described in the repository's `HA_ADDON_PATTERNS.md`; this file covers what's specific to chat.

A private chat and file-sharing app for the household, inside Home Assistant. Each person is their Home Assistant user. It has no accounts of its own, and nobody outside Home Assistant can reach it.

## 1. Purpose & scope

- **People.** Every HA user linked to a Person is listed automatically, but **disabled by default**: nobody can read or send anything until an admin **enables** them (Admin → People). Enabling needs no password; the HA login is the identity. Disabling cuts access at once (§4).
- **Direct chats.** Between two enabled people, one per pair, started from the people list.
- **Group chats.** Any number, each with a name, an emoji and members. The creator is the group's **owner** and can make others **group admins**. By default anyone enabled may create a group (App setting, §3.1).
- **A personal room ("My room")** for every enabled person: a chat with only themselves, for notes, links and documents that nobody else can see (§17).
- **A "Household" group** is created with the first enabled person. Everyone enabled joins it automatically and can leave it.
- **Messages.**
  - Text with line breaks, emoji, auto-linked URLs, simple formatting (§16.4) and @mentions (`@everyone` in groups).
  - Replies (quote), edit (within the edit window, shown as "edited"), delete (leaves "message deleted"), reactions.
  - Read state per person per chat ("Seen by Nisha, Tarun"), unread counts, a typing indicator, online status.
- **Files and documents.**
  - Attach any file to a message: drag and drop, paste, the 📎 button, or the phone's camera.
  - **Stored as ordinary files in Home Assistant's `/share` folder** (the chat files folder, `/share/household_chat` by default, §5.3), so they can also be reached over Samba or the File editor.
  - Each chat has a **Files** view listing everything shared in it, and **My files** lists everything you've shared.
  - Images show inline with thumbnails. PDFs, text and images open in a viewer; everything else downloads.
  - Photos are made smaller unless you choose to send the original (§15.4). **Voice messages** (§15.6).
- **Pinned messages** (§15.2), **polls** (§15.5), **announcements** (§15.7), **disappearing messages** (§15.8), **chat downloads** (§15.9), **storage clean-up** (§15.10).
- **Remind me** (§16.1), **starred messages** (§16.2), **forward** (§16.3), **chat descriptions** (§16.6), **child accounts** (§16.5), **send later** (§16.7), **drafts across devices** (§16.8).
- **Home / away** next to people's names and **photos**, from Home Assistant (§15.3, §15.3.1).
- **Reply from the phone notification** without opening Home Assistant (§15.1).
- **Voice calls** in direct chats, optional, on the home network (§15.12).
- **Search** across the messages and file names of the chats you're in.
- **Shared folders**: existing `/share` folders shared into chats (§12).
- **Notifications** to phones through HA notify (§7).
- **Out of scope.**
  - Video calls, and voice calls from outside the home network (planned, §18.1).
  - End-to-end encryption: messages are in the app's SQLite database and files are plain files in `/share` (§4.1 says who can read them).
  - Access from outside HA (no public link sharing), and chatting with people who aren't HA users.
  - Bots, link previews (they'd fetch every shared link), GIFs and stickers, and an unread badge on HA's sidebar (app panels can't show one).
  - Other apps or HA automations posting into chats.

### 1.1 Why a separate app

- **Different kind of app.** Chat keeps a live connection open per open tab (§8) and is used all day. The sibling apps are request/response pages, opened now and then.
- **Security boundaries.** Household Vault is hardened on purpose (encrypted at rest, strict session rules). Chat stores readable messages and plain files in `/share`, so it must not live inside the Vault.
- **Its own panel and permissions.** It needs `share:rw` and its own sidebar entry ("Chat").
- **Independent updates and backups.** The chat database grows fast; keeping it separate keeps the other apps' backups small, and a chat bug can't take them down.

It still fits the family: the same look and themes, the same shared code from the repository's `common/` folder (`ha_notify.py`, `ha_people.py`, the admin pages, …), the same admin pattern.

## 2. Stack & file layout

- **Backend.** Python 3.12 on `python:3.12-alpine` + `tzdata`; FastAPI + uvicorn (pinned, the same set as the sibling apps); raw `sqlite3` at `/data/chat.db` in WAL mode, with FTS5 for search.
- **Images.** **Pillow** for thumbnails, resizing, EXIF rotation and people's photos.
- **Frontend.** Plain HTML/CSS/JS, no build step, no libraries; `h()` DOM building only (no `innerHTML`); strict CSP.
- **Live updates.** Server-Sent Events over ingress (§8), with polling as the fallback.

```
household_chat/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore
├── README.md  DOCS.md  icon.png  logo.png  translations/en.yaml  spec/SPEC.md
├── app/
│   ├── main.py            # lifespan (folder check, expiry pass, HA time zone, person sync, photos, jobs),
│   │                      # routers, ingress-IP guard, security headers (CSP), static files
│   ├── config.py          # options.json → ADMIN_NAMES; paths (DATA_DIR, SHARE_DIR); time helpers in HA's zone (config.ZONE)
│   ├── settings.py        # App settings (§3.1), declared on common settings_core
│   ├── auth.py            # get_current_user, require_user, require_admin (on common auth_core)
│   ├── db.py              # schema, MIGRATIONS of older databases, helpers (on common db_core)
│   ├── ha_client.py       # thin: re-exports the shared Core API client + person_entities, user sync, load_time_zone_blocking
│   ├── ha_events.py       # the notification buttons (§15.1), on the shared WebSocket client common/ha_ws.py;
│   │                      # the same one connection carries the app messages (app_messages.py)
│   ├── app_messages.py    # messages from the other household apps: chat.chats.list, chat.card (§15.11)
│   ├── notifier.py        # who gets told what, batching, quiet hours, "already looking" (§7)
│   ├── live.py            # SSE hub: per-user queues, events, presence/typing (§8)
│   ├── chats.py           # rules and serialisation of chats and messages
│   ├── files.py           # the chat files folder, names, thumbnails, serving rules (§5.3)
│   ├── presence.py        # home/away (§15.3)
│   ├── avatars.py         # people's photos (§15.3.1)
│   ├── disappearing.py    # the expiry pass (§15.8)
│   ├── scheduled.py       # send later (§16.7)
│   ├── exporter.py        # chat downloads (§15.9)
│   ├── shared_folders.py  # shared folders (§12)
│   ├── housekeeping.py    # background jobs (§10)
│   ├── routers/           # me, conversations, messages, files, search, stream, extras, admin
│   ├── common/            # shared Python (copies): ha_notify, ha_people, whoami, ha_client, ha_time, housekeeping,
│   │                      # ha_ws, app_bus, auth_core, db_core, settings_core, people_admin, web_security, backup_core
│   └── static/            # index.html, core.js, format.js, chat.js, compose.js,
│       │                  # dialogs.js, extras.js, pages.js, main.js, style.css
│       └── common/        # shared browser files (copies): theme-boot.js, themes.css, ui.js, settings.js,
│                          # settings.css, people.js, whoami.js, connected-apps.js (no backnav.js: Chat has its
│                          # own layers, §9)
└── tests/                 # _env.py, base.py, test_*.py; common_tests/ (shared helpers and shared-module tests, copies:
                           # also fake_ha.py, fake_ha_bus.py and test_app_bus.py)
```

`app/common/`, `app/static/common/` and `tests/common_tests/` are copies of the repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see `common/README.md`). Never edit a copy: edit `common/` and re-sync (`tests/common_tests/test_shared_copies.py` fails if a copy was changed).

## 3. Manifest & options

**Manifest.**
- `slug: household_chat`, `name: Household Chat`, `url: https://github.com/sameerkotra/ha-apps`.
- Ingress: `ingress: true`, `ingress_port: 8103`, **no `ports:`**.
- Panel: `panel_icon: mdi:chat-processing`, `panel_title: Chat`, `panel_admin: false` (everyone sees the panel; nobody gets in until enabled).
- Permissions: `homeassistant_api: true` (persons, states, notify, services, the time zone, the notification-action events); `map: - share:rw`; every other privilege false; `apparmor: true`.
- Arch: amd64, aarch64, armv7. Built on install from the `Dockerfile`.

**Option.** Only `admin_users` (HA login names or user ids, never display names), empty by default. It's read at start-up, so a change needs a restart. Everything else is an App setting.

**Environment.** `DATA_DIR=/data`, `SHARE_DIR=/share/household_chat` (only the default of the `files_path` App setting; tests override both), `PORT=8103`.

**Time zone.** Home Assistant's zone is read at start-up (`GET /api/config`) and used for quiet hours, reminder shortcuts, send-later times, chat-download dates and the nightly job. Without it, UTC.

### 3.1 App settings (Admin → App settings; `app_settings` table; validated; live)

| Key | Default | Meaning |
|---|---|---|
| `files_path` | `/share/household_chat` | The chat files folder (§5.3.1): an absolute, normalised path inside `/share` (not `/share` itself; no `.`/`..`). Put it on network storage with `/share/<mount>/household_chat`. Changing it never moves files; it's checked first and refused or confirmed as §5.3.1 says. A restore keeps this install's value. |
| `max_upload_mb` | 50 | Largest single file (1–1024). Enforced before the body is read (Content-Length) and while streaming. |
| `share_folder_quota_gb` | 0 | 0 = no limit. Uploads are refused once the chat folder would pass it. |
| `who_can_create_groups` | `everyone` | `everyone` or `admins`. |
| `edit_window_minutes` | 15 | How long a message can be edited (0 = never, max 1440). |
| `message_retention_days` | 0 | 0 = keep forever. Older messages (except pinned ones) are deleted nightly. Their files are deleted too, unless `keep_files_on_retention` is on. |
| `keep_files_on_retention` | true | See above. |
| `retention_includes_personal` | false | Whether `message_retention_days` also deletes from personal rooms (§17). |
| `notify_preview` | `full` | `full` (sender + text), `sender` ("New message from Nisha"), or `none` ("New message in Household Chat"). Also capped per person (§7). |
| `blocked_extensions` | `exe,bat,cmd,com,scr,msi,ps1,vbs,js,jar,apk,html,htm,svg` | Uploads with these extensions are refused. |
| `photo_max_px` | 2000 | Photos are resized so the longest side is at most this (0 = never). *Send original* skips it (§15.4). |
| `voice_max_seconds` | 300 | Longest voice message (10–900) (§15.6). |
| `show_presence` | true | Show home/away from HA next to names (§15.3). |
| `notification_reply` | true | Offer *Reply* and *Mark as read* on phone notifications (§15.1). |
| `who_can_announce` | `admins` | `admins`, or `admins_and_group_admins` (§15.7). |
| `children_can_message_each_other` | false | §16.5. |
| `export_max_mb` | 500 | Largest chat download, files included (§15.9). |
| `calls_enabled` | false | Voice calls (§15.12). Off: no 📞, and the call routes answer 403. |
| `calls_ring_seconds` | 30 | How long a call rings before it's missed (15–60; shown while calls are on). |

The settings are declared once in `settings.py` (`SETTINGS`, `GROUPS`: files, messages, retention, notifications, calls) on the shared `settings_core.Registry` (`app/common/settings_core.py`). `GET/PUT /api/admin/settings` → `{values, defaults, meta, groups, storage}` (`meta[key]` = label, help, group, kind, `restartRequired`, range, choices, …; `storage` as in §5.3.1); unknown keys and out-of-range values answer 422 and nothing is saved. Values are read through a 5-second cache, so changes apply without a restart. `files_store_id` (§5.3.1) is also kept in `app_settings` but isn't a setting.

## 4. Security & identity

- **Ingress only.** A middleware answers 403 to any request whose TCP source isn't Supervisor's ingress proxy (`172.30.32.2`) or loopback (`auth_core.INGRESS_HOSTS`, shared `app/common/auth_core.py`), before any route runs. The security headers (CSP, `nosniff`, Cache-Control) are added at the end of that guard by `web_security.SecurityHeaders.apply` (shared `app/common/web_security.py`), the same headers as before. After that, the caller is identified by `X-Remote-User-Id` (401 without it), with `X-Remote-User-Name` (login) and `X-Remote-User-Display-Name`.
- **Admins** are the people whose user id or login name (case-insensitive) is in `admin_users`. Display names are never matched; when an entry matches someone only by display name, `/api/me` and `/api/whoami` say so (`displayNameOnly`) and "How the app sees you" tells them what to add instead.
- **Users.**
  - The user list mirrors HA persons: synced at start-up, every 5 minutes and on *Check Home Assistant again*. Someone who opens the app without a Person is added on first visit.
  - **Everyone starts disabled.** A disabled person sees "Ask an admin to give you access" and the "How the app sees you" page; every other API call returns 403 (`/api/me` and `/api/whoami` still answer).
  - **Enable** (admin): the person can chat, gets their **personal room** (§17), and joins the **Household** group (made with the first enabled person). A group's owner or group admins add them to other groups; being an app admin doesn't let you add people to (or join) a group you aren't in.
  - **Disable** (admin): the person's live connections close; they can't read anything. Their messages stay, shown as from "Tarun (no longer here)". They leave every group; a group they owned passes to its oldest remaining group admin, else its oldest member. Direct chats with them become read-only for the other person.
  - **Re-enable:** back into their direct chats, their room and the Household group (not other groups; people add them again).
- **Who sees what.** You see a chat only if you're a current member. Everyone else gets 404 (existence isn't leaked).
  - Leaving a group or being removed hides it at once, including its files through the app. What you already downloaded stays with you.
  - **Admins can't read chats they aren't in.** Being an app admin gives no access to any chat's messages, files, search results, polls, pins, shared-folder links or downloads. An admin sees a chat's content only as a member, like everyone else, and admin routes never return message text or file contents.
    - To run the app, admins see **metadata only** (Admin → Chats): each chat's name, kind, members, created and last-activity dates, message count and storage used. File names aren't shown for chats the admin isn't in (§15.10).
    - An admin can't join, or add anyone to, a group they aren't in. Only the group's owner and group admins can (§5.2). Membership changes always post a visible system message.
    - An admin can **delete a group only when it has no enabled members left**, confirmed by typing its name, recorded in `audit_log` (`admin_deleted_chat`). Otherwise deleting stays with the group's owner.
    - DOCS says plainly what admins can and can't see, and that anyone with access to the Home Assistant host, `/share` or backups can still read everything (§4.1). The app's rule isn't encryption.
- **Files through the app** are served only to current members of the chat they were shared in:
  - Content types are fixed server-side from an allow-list.
  - Images (png, jpeg, gif, webp), audio (webm, ogg, mp4/m4a, mpeg) and plain text are served inline, with `Content-Security-Policy: sandbox; default-src 'none'`. PDFs are served inline without the sandbox (Chrome's PDF viewer refuses sandboxed documents; a PDF can't run script on HA's origin) and open in an `<iframe>` without the sandbox attribute. Everything else gets `Content-Disposition: attachment`.
  - Always `X-Content-Type-Options: nosniff`. SVG, HTML and XML are never served inline (they could run script on HA's origin).
- **Browser hardening.**
  - CSP `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'`; `Referrer-Policy: no-referrer`; `Cache-Control: no-store` on the API.
  - No `innerHTML`; message text always goes in through `textContent`; links go through a `safeUrl()` (http/https/mailto/tel only), with `rel="noopener noreferrer"`.
  - Request bodies over 1 MB are refused (413), except uploads and restores, which enforce their own limits while streaming.
- **Rate limits.** 30 messages and 20 uploads a minute per person.
- **The server never logs** message text or file contents.

### 4.1 Who else can read chats and files (stated plainly in DOCS)

| Where | Who can read it |
|---|---|
| Messages in the app | The chat's current members only. App admins can't read chats they aren't in; they see chat names, members, dates and sizes (§4). |
| Messages in `/data/chat.db` | Anyone with the HA host, SSH, or backups: HA backups include `/data`, and the app's own backup (Admin → Storage, admins only) is a copy of the database. Not encrypted at rest, so **an admin who downloads a backup or opens the host can read it outside the app**. The app keeps admins out; it can't keep out someone who controls the machine. |
| Files in the chat files folder | Anyone who can reach `/share`: Samba/NFS app users, the File editor, other apps with `share` access, and HA backups that include Share. Folder and file names show chat names (§5.3). |
| Notifications | Whatever `notify_preview` allows appears on lock screens and in the HA Companion app's history, and travels through the Companion app's push service like any HA notification. Disappearing messages never show their text there (§15.8). |
| Notification replies | Pass through Home Assistant's event bus (§15.1): visible to HA admins and automations. |
| Home / away | Taken from the `person.*` entity linked to each person's login (or one an admin chose instead); HA already shows every HA user those states (§15.3). |

**So:** fine for household chat and documents; not for secrets. DOCS points to Household Vault for passwords and card numbers. Apart from notifications, the app talks only to Home Assistant; people's pictures are fetched only from HA itself (§15.3.1).

### 4.2 First run: "No admin yet"

- With `admin_users` empty, nobody is an admin, so nobody can enable people or open App settings. Nobody is ever promoted automatically — not the first visitor, not anyone.
- `GET /api/me` and `GET /api/whoami` carry `noAdmin: true` (`whoami` also `adminEntries: 0`).
- The page then shows a banner to everyone, on every page (it sits above the app, so it's there on the chat list, on "Ask an admin to give you access" and in the admin view; drawn by `HouseholdWhoami.noAdminBanner` from `common/whoami.js`): "**No admin yet** — add your Home Assistant user name (**<their login name>**) to `admin_users` on the app's Configuration tab, save, and restart the app." with a link to "How the app sees you" (a page for enabled people, a dialog for everyone else). "How the app sees you" repeats the hint.

## 5. Data model

### 5.1 SQLite (`/data/chat.db`)

The core tables (feature tables are listed in their sections; `app/db.py` holds the full schema):

```sql
CREATE TABLE users (id TEXT PRIMARY KEY,               -- HA user id
  name TEXT NOT NULL, username TEXT,
  ha_person TEXT,                                      -- the person.* linked to their login (person sync)
  disabled INTEGER NOT NULL DEFAULT 1,                 -- everyone starts disabled
  is_child INTEGER NOT NULL DEFAULT 0,                 -- §16.5
  avatar_file TEXT, avatar_src TEXT, avatar_version INTEGER NOT NULL DEFAULT 0,   -- photo copy (§15.3.1)
  presence_entity TEXT,                                -- person.* an admin chose instead of ha_person (§15.3); NULL = automatic
  notify_level TEXT NOT NULL DEFAULT 'direct_mentions' CHECK (notify_level IN ('all','direct_mentions','off')),
  notify_preview TEXT NOT NULL DEFAULT 'full' CHECK (notify_preview IN ('full','sender','none')),
  quiet_start TEXT, quiet_end TEXT,                    -- "22:00"/"07:00" in HA's time zone, or NULL
  hide_online INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT, created_at TEXT NOT NULL);
CREATE TABLE user_notify (user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  service TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT, PRIMARY KEY (user_id, service));   -- extras (§7)
CREATE TABLE conversations (id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('direct','group','personal')),
  name TEXT,                                           -- groups only, 1–60 chars
  icon TEXT,                                           -- an emoji (groups)
  description TEXT,                                    -- §16.6
  direct_key TEXT UNIQUE,                              -- direct: "<smaller id>|<larger id>"
  folder TEXT NOT NULL,                                -- its folder in the chat files folder (§5.3)
  created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL,
  last_message_id INTEGER, last_activity_at TEXT,
  is_household INTEGER NOT NULL DEFAULT 0,             -- the one "Household" group
  new_members_see_history INTEGER NOT NULL DEFAULT 1,
  disappear_seconds INTEGER);                          -- §15.8
CREATE UNIQUE INDEX idx_one_household ON conversations(is_household) WHERE is_household = 1;
CREATE UNIQUE INDEX idx_one_personal ON conversations(created_by) WHERE kind = 'personal';   -- one room per person (§17)
CREATE TABLE members (conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner','admin','member')),
  joined_at TEXT NOT NULL, joined_message_id INTEGER NOT NULL DEFAULT 0,   -- history visible after this id
  last_read_id INTEGER NOT NULL DEFAULT 0,
  notify TEXT NOT NULL DEFAULT 'default' CHECK (notify IN ('default','all','mentions','off')),
  muted_until TEXT, pinned INTEGER NOT NULL DEFAULT 0,   -- pinned = chat pinned to the top of my list
  PRIMARY KEY (conversation_id, user_id));
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,   -- global order; paging by id
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_id TEXT REFERENCES users(id),                   -- NULL for system messages
  kind TEXT NOT NULL CHECK (kind IN ('text','file','system','poll','card','call')),   -- card: §15.11; call: §15.12
  body TEXT NOT NULL DEFAULT '',                       -- ≤ 8000 chars; system: JSON {event, …}; card: its title; call: ''
  reply_to INTEGER REFERENCES messages(id) ON DELETE SET NULL, reply_gone INTEGER NOT NULL DEFAULT 0,
  mentions TEXT, mention_all INTEGER NOT NULL DEFAULT 0,   -- server-resolved user ids; @everyone
  forwarded INTEGER NOT NULL DEFAULT 0, via TEXT,      -- via = 'notification' for replies from the phone
  created_at TEXT NOT NULL, edited_at TEXT, deleted_at TEXT,
  pinned_at TEXT, pinned_by TEXT,                      -- §15.2
  expires_at TEXT,                                     -- §15.8
  announcement INTEGER NOT NULL DEFAULT 0, announcement_closed_at TEXT, announcement_reminded_at TEXT);   -- §15.7
CREATE TABLE attachments (id TEXT PRIMARY KEY,
  message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,   -- NULL while pending (uploaded, not sent)
  conversation_id TEXT NOT NULL, rel_path TEXT NOT NULL,           -- relative to the chat files folder
  original_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, original_size INTEGER,
  sha256 TEXT NOT NULL, width INTEGER, height INTEGER, duration REAL, voice INTEGER NOT NULL DEFAULT 0,
  uploaded_by TEXT, created_at TEXT NOT NULL,
  missing INTEGER NOT NULL DEFAULT 0,                  -- deleted/moved outside the app
  admin_deleted_at TEXT,                               -- §15.10
  scheduled_id TEXT);                                  -- belongs to a scheduled message (§16.7)
CREATE TABLE heard (attachment_id, user_id, at);       -- voice messages played
CREATE TABLE reactions (message_id, user_id, emoji, created_at, PRIMARY KEY (message_id, user_id, emoji));
CREATE VIRTUAL TABLE messages_fts USING fts5(body, content='messages', content_rowid='id');   -- kept by triggers
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT, updated_by TEXT);
CREATE TABLE audit_log (id TEXT PRIMARY KEY, user_id TEXT, actor_id TEXT, conversation_id TEXT,
  action TEXT NOT NULL, created_at TEXT NOT NULL);     -- admin and membership actions, never content
CREATE TABLE app_cards (message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,   -- §15.11
  app TEXT NOT NULL, badge TEXT NOT NULL,
  item_type TEXT NOT NULL CHECK (item_type IN ('note','checklist','sheet','folder','file')),
  item_id TEXT NOT NULL, title TEXT NOT NULL, owner_name TEXT,
  panel TEXT, target TEXT,                             -- the other app's page and the item's route in it
  shared_with_members INTEGER NOT NULL DEFAULT 0, bus_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE calls (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,   -- §15.12
  caller_id TEXT NOT NULL, callee_id TEXT NOT NULL, started_at TEXT NOT NULL, answered_at TEXT, ended_at TEXT,
  outcome TEXT CHECK (outcome IN ('answered','missed','declined','busy','failed')),   -- NULL while on
  message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE);   -- its note
-- bus_outbox, bus_seen, bus_apps: the app messages (APP_MESSAGES_SPEC.md §4, created by app_bus.migrate)
```

- Connections (`db_core.connect` / `db_core.transaction`, shared `app/common/db_core.py`: timeout 30, `check_same_thread=False`) use `foreign_keys = ON`, `busy_timeout = 30000` and `secure_delete = ON` (§15.8).
- **Older databases** are brought up to date at start-up: missing columns are added (`db.MIGRATIONS`, applied by `db_core.add_missing_columns`), the old one-row-per-group shared folder table becomes `folders` + `folder_links` (§12), photos people once uploaded themselves and chat photos are removed (§15.3.1), and a 🏠 choice equal to the person's own `ha_person` becomes automatic. After an admin restore the same runs again.
- **`messages.kind` gains `'card'`** (cards, §15.11) (`db._allow_card_messages`). SQLite can't change a CHECK in place, so the table is rebuilt the way SQLite documents it: foreign keys off; in one transaction a copy made from the stored `CREATE TABLE` text with only the CHECK changed (so every column, including those added later by `ALTER`, keeps its place), every row copied with its id, the old table dropped, the copy renamed, its indexes and triggers re-created from their stored SQL, the AUTOINCREMENT counter kept (ids of deleted messages are never reused), the row count compared and `PRAGMA foreign_key_check` required to find nothing new — otherwise everything is rolled back and the old table stays; foreign keys back on. Nothing that points at messages changes (same ids); the search index keeps its rowids. Chosen over "store cards as an existing kind plus a JSON column" because every rule that looks at `kind` (edit, forward, copy text, previews, notifications) would otherwise treat a card as a text message. Runs once (it does nothing when `'card'` is there), also after a restore of an older backup.

### 5.2 Rules

- **Direct chats.** Unique per pair (`direct_key`). Created when opened, but the other person doesn't see one until it has a message. They can't be renamed, and nobody can be added; to add a third person, start a group.
- **Personal rooms** (§17). One per person, one member (its owner) for ever.
- **Groups.**
  - Owner and group admins can rename, change the emoji and description, add or remove people, make or unmake group admins, and change "new members see history".
  - Anyone can leave. The owner can hand over ownership (`/transfer`); if they leave, it passes to the oldest group admin, else the oldest member.
  - The owner can delete a group for everyone, confirmed by typing its name. This deletes its messages; its files are moved to `_deleted/…` and purged after 30 days.
- **System messages** record joins, leaves, renames, ownership changes, pins, description changes and disappearing-setting changes ("Meera added Tarun"). Personal rooms have none.
- **Limits.** 20 members per group; 100 groups; message text up to 8000 characters; 10 files per message; pages of 50 messages (`before=`, `after=`, `around=`).
- **Edits and deletes.**
  - Only the author edits, within the edit window. The last text is kept, with no edit history.
  - A delete clears the text (`deleted_at`), keeps the row so replies still make sense, removes its pin, and moves the message's files to `_deleted`. The author can delete their own; a group's owner or group admins can delete anyone's in that group (being an app admin doesn't count).
- **Retention** (§3.1) hard-deletes old rows (replies keep their text) but skips pinned messages.

### 5.3 Files in the chat files folder (default `/share/household_chat/`)

```
/share/household_chat/
├── README.txt                                  # what this folder is; don't rename files inside
├── .household_chat_store                       # marker: this install's store id (§5.3.1)
├── Family (a1b2c3d4)/2026-09/Electricity bill.pdf
├── Family (a1b2c3d4)/2026-09/IMG_2041.jpg
├── Family (a1b2c3d4)/_disappearing/20261001T1800Z__Code.jpg   # §15.8
├── Meera & Nisha (9f8e7d6c)/2026-10/Lease.pdf
├── Nisha - personal (5e4d3c2b)/2026-10/Passport scan.pdf
├── _thumbs/…                                   # generated, can be deleted any time
└── _deleted/…                                  # from deleted groups and messages, purged after 30 days
```

- **Folder names.**
  - A group's folder is `<name> (<first 8 of its id>)`, cleaned for Windows/Samba (no `\/:*?"<>|`, no trailing dot or space, ≤ 60 characters).
  - A direct chat's folder is `<A> & <B> (<id8>)`; a personal room's is `<name> - personal (<id8>)`, renamed if the person's HA name changes.
  - Renaming a group renames its folder in one `os.rename`, and every `rel_path` is updated in the same transaction. A file uploaded while the chat was renamed is moved into the new folder when it's recorded.
- **File names** keep the original name (cleaned the same way). A clash gets ` (2)`, ` (3)`… The month folder keeps a busy chat browsable.
- **Uploads** are the raw request body (`POST /api/conversations/{id}/uploads?name=…&original=…&voice=…&duration=…`), not multipart, so the size limit is enforced while streaming and the browser shows progress. The file is streamed to a temporary file in the chat's month folder (size and quota checked as it goes, SHA-256 computed), then `os.replace`d. It stays pending (`message_id IS NULL`) until sent; `DELETE /api/uploads/{id}` cancels it, and unsent uploads older than 24 h are removed. Images get their size and EXIF orientation read with Pillow and a 320 px thumbnail in `_thumbs` (the original is never modified, except when made smaller on upload, §15.4).
- **Reading.** Always resolve `<folder> / rel_path`, `realpath` it, and refuse anything outside the folder (symlinks included). Never trust a path from the client; clients use attachment ids.
- **Changed outside the app.** If a file is renamed or deleted over Samba, the attachment shows as "File no longer available" (`missing = 1`, found on access and by the nightly check). Files dropped into these folders by hand are ignored (shared folders, §12, are the way to bring `/share` files into chats).
- **Backups.**
  - HA's own backups include `/share` when "Share" is ticked.
  - The app's admin backup (`GET /api/admin-storage-download-db`) is a copy of the database (`Connection.backup`, `db_core.snapshot`), with an *Include files* option that builds a zip including the chat files folder (`backup_core.write_zip` / `walk`, sent by `backup_core.send_file`). It leaves out disappearing messages and their files (§15.8) and scheduled messages that will disappear.
  - Restore (`POST /api/admin-storage-import-db`, up to 20 GB; received by `backup_core.receive`) validates the zip (member names and the app's own checks: `backup_core.check_members`; files put back with `backup_core.copy_out`; its own swap with `RESTORING` is unchanged), answers 503 to other requests and pauses background jobs while the database file is swapped, runs the migrations and the expiry pass, puts files back if the zip has them, re-checks `missing`, and tells open pages to reload. It keeps this install's `files_path` and `files_store_id`.

### 5.3.1 Choosing the folder, and "connected"

- **Setting.** `files_path` in Admin → App settings (§3.1), read live (`files.configured()`). Everything that uses the folder follows it: uploads, serving, previews, `_deleted`, `_disappearing`, forwarding, chat downloads, backups with files, restores, clean-up and the nightly jobs. People's photos stay in `/data` (§15.3.1). Shared folders can't be the chat files folder, and the chat files folder can't be (inside, or contain) a shared folder.
- **Marker.** `<folder>/.household_chat_store` holds this install's store id, also kept in `app_settings` as `files_store_id`. The database stores paths relative to the folder, so the folder can move. A backup never contains the marker.
- **Connected** means: the folder exists, its marker matches `files_store_id`, and it's writable. It's checked **at start-up** (before the expiry pass), **every 5 minutes**, **right after an admin changes the folder**, on *Check again*, and by a request for a file when the last check is over 30 s old. Each successful check also makes sure `_thumbs/`, `_deleted/` and `README.txt` exist.
- **Never set up yet** (a new install, or an older one without `files_store_id`): the folder is created if its parent exists and gets a marker — or, if it already has one, that marker becomes this install's. This is the only time a routine check sets anything up.
- **Not connected** (NAS off, mount failed, marker lost or from another install, read-only):
  - The app never writes into the folder: an unmounted NAS path would otherwise fill HA's own disk and be hidden once the mount returns.
  - Chats, messages and everything in the database keep working. Uploads, downloads, previews, forwarding files, chat downloads with files, backups with files and restores with files answer **503** with the reason. Files are never marked missing because of it.
  - Deletes asked for meanwhile (messages, groups, unsent uploads, disappearing files) wait in `/data/pending_file_jobs.json` (relative paths only) and run when it's back; group folder renames are applied then too, and `missing` is re-checked.
  - Everyone sees a banner "File storage isn't connected" in the chat list; admins get the reason in Admin → Storage and App settings. A `storage` live event updates open pages when the state changes.
- **Changing the folder** (files are never moved). *Check folder* (`POST /api/admin/settings/check-files-path {path}`, admin-only, no side effects) returns `{exists, parentExists, writable, marker: none|this|other, files (found on disk), dbFiles, verdict, message, ok, needsConfirm, refused, networkMount}`. Verdicts:
  - *current* — the folder in use.
  - *this* — it has this install's marker (e.g. the old folder, copied whole): safe.
  - *new* — no chat files yet: the folder is created (if needed) with what the app needs when you save.
  - *no_marker* — chats already have files: they stay in the old folder and show as "no longer available" until copied; needs confirmation.
  - Refused: *other* (another install's marker), *shared* (a shared folder), *read_only*, *missing_parent* (neither the folder nor its parent exists — is the storage mounted?).
  - `PUT /api/admin/settings` enforces the same (409 for refused, and for *no_marker* without `"confirm": true`), then runs the check with set-up allowed: the folder is created, the marker written and the layout made, and it's connected at once.
- **Use this folder** (Admin → Storage, only while not connected; `POST /api/admin/files-storage/check {useThisFolder: true}`, confirmed): sets up the folder in use now for this install when its marker was lost or came from another install. *Check again* is the same call without it. Nothing is moved or deleted.
- **Status.** `GET /api/admin/settings` and `GET /api/admin/storage` carry `storage {path, online, reason, checkedAt, networkMount, freeBytes, default}`; `/api/me` carries `files {online, reason}` without the path.
- **Admin UI.** The chat files folder is a field of App settings' **Files** group (in use now, ● Connected / Not connected, path box, *Check folder*; on a phone the path box has its own line and long paths wrap), saved with the other settings by **Save** — the separate *Change folder* button is gone; a change is checked first and refused or confirmed as above — and a *File storage* card at the top of Storage (folder, status, last check, free space, waiting deletes, *Check again*, *Use this folder*, *Change folder…*).

## 6. API (all `/api`, ingress; 403 when disabled; 404 for chats you aren't in)

| Method & path | Purpose |
|---|---|
| GET `/me` · PUT `/me/settings` | Me (`isAdmin`, `disabled`, `isChild`, `personalRoomId`, `avatar`, `notifyLinked`, `files`, `noAdmin`, `timeZone`, public App settings, `version`) + my notification choices, quiet hours, hide online |
| GET `/whoami` | The shared contract (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`): `haUserId`, `haUsername`, `haDisplayName`, `nameSent`, `isAdmin`, `displayNameOnly`, `adminEntries`, `noAdmin`, `viaIngress`, `notifyLinked`, `extras` (Access, Chats you're in, Phone linked for notifications), plus the app's `disabled` (works when disabled) |
| GET `/people` · GET `/avatars/{id}` · POST `/presence` | Enabled people `{id, name, avatar, lastSeen, …}`; a person's photo (§15.3.1); my visible chat and heartbeat (§8) |
| GET `/conversations` | Mine: my personal room first, then pinned, then newest activity: `{id, kind, name, icon, members, lastMessage{preview, at, by}, unread, mentionUnread, muted, pinned, draft, …}` |
| GET `/conversations/{id}` | Details: members and roles, description, pins, announcements, folders, what I may do |
| POST `/conversations/direct` `{userId}` | Get or create the direct chat (your own id returns your room) |
| POST `/conversations` `{name, icon, memberIds}` | New group |
| PATCH `/conversations/{id}` | Name, icon, description, `newMembersSeeHistory`, `disappearSeconds` |
| POST / DELETE / PATCH `/conversations/{id}/members[/{uid}]` · POST `/leave` · POST `/transfer` | Membership and roles |
| PUT `/conversations/{id}/me` `{notify, mutedUntil, pinned}` | My per-chat settings |
| POST `/conversations/{id}/read` `{upTo}` · POST `/typing` | Read marker; typing ping (≤ 1 every 3 s) |
| DELETE `/conversations/{id}` `{confirmName}` | Owner deletes a group |
| GET / POST `/conversations/{id}/messages` | A page (50) with reactions, reply previews, attachments, read-by; post `{body, replyTo, attachmentIds, expiresIn, announcement}` |
| PATCH / DELETE `/messages/{id}` | Edit (author, window) / delete |
| PUT / DELETE `/messages/{id}/reactions/{emoji}` · `/pin` · `/star` | React, pin, star |
| POST / DELETE `/messages/{id}/remind` | Remind me (§16.1) |
| POST `/messages/forward` | Forward (§16.3) |
| POST `/messages/{id}/ack` · `/announcement-reminder` · `/announcement-close` | Announcements (§15.7) |
| POST `/conversations/{id}/polls` · PUT `/polls/{id}/vote` · POST `/polls/{id}/close` | Polls (§15.5) |
| POST `/conversations/{id}/uploads` · DELETE `/uploads/{id}` | Upload (raw body, §5.3) / cancel |
| GET `/files/{id}` · `/files/{id}/thumb` · `?download=1` · POST `/files/{id}/heard` | Serve (§4); voice message played |
| GET `/conversations/{id}/files?type=` · GET `/me/files` · GET `/conversations/{id}/pins` | Files lists; pins |
| GET `/search?q=&conversation=` | Messages (FTS5, snippets) and file names, only in my chats |
| GET `/me/starred` · GET / DELETE `/me/reminders[/{id}]` | Starred and reminders |
| PUT / DELETE `/conversations/{id}/draft` | Drafts (§16.8) |
| POST `/conversations/{id}/scheduled` · GET `/me/scheduled` · PATCH / DELETE `/scheduled/{id}` · POST `/scheduled/{id}/send-now` | Send later (§16.7) |
| GET `/conversations/{id}/export?from=&to=` | Download a chat (§15.9) |
| GET `/conversations/{id}/folders` · GET `/folders/{linkId}/list\|file\|search` · POST `/folders/{linkId}/upload\|mkdir` | Shared folders (§12) |
| POST `/calls` · POST `/calls/{id}/offer\|answer\|candidate\|decline\|end` · GET `/calls/current` | Voice calls (§15.12) |
| GET `/stream` | Server-Sent Events (§8) |
| GET `/health` | `{status, version}` |
| Admin: GET `/admin/people[?refresh=1]` · PATCH `/admin/people/{id}` `{disabled, isChild}` · POST / DELETE `/admin/people/{id}/notify[/{svc}]` · POST `/admin/people/{id}/notify/test` · GET `/admin/notify-services` · GET `/admin/person-entities` · PUT `/admin/people/{id}/presence` | People, notify extras, home/away (§7, §15.3) |
| Admin: GET / PUT `/admin/settings` · POST `/admin/settings/check-files-path` · POST `/admin/files-storage/check` | App settings, the chat files folder (§5.3.1) |
| Admin: GET `/admin/connected-apps` | `{on, connected, apps: [{slug, name, version, can, last_seen, active}]}` — Connected apps (§15.11), read only |
| Admin: GET `/admin/conversations` · DELETE `/admin/conversations/{id}` | Chat overview, metadata only; delete only a group with no enabled members; logged |
| Admin: GET `/admin/storage` · POST `/admin/storage/check` · GET `/admin/storage/usage\|files` · POST `/admin/storage/delete\|empty-deleted\|thumbs-delete` · GET `/admin-storage-download-db` · POST `/admin-storage-import-db` | Storage, clean-up, backup and restore (§5.3, §15.10) |
| Admin: GET `/admin/share-dirs` · GET / POST `/admin/folders` · PATCH / DELETE `/admin/folders/{id}` | Shared folders (§12) |

No admin route returns message text, file names or file contents of chats the admin isn't in.

## 7. Notifications (`notifier.py` + the shared `ha_notify.py` and `ha_people.py`)

- **Phones.** From Home Assistant: the person linked to the login (Settings → People → *Allow person to login*) and the Companion-app phones picked under *Track device*, read by `ha_people.py`. One `POST /api/template` renders every `person.*` with its linked `user_id`, state and picture, and each tracked device that belongs to the `mobile_app` integration. A device's notify action is `mobile_app_<slugified device name>` (or HA's numbered `_2…` one), checked against `GET /api/services`; a device with none is shown (⚠ "Companion app action not found") but not used. Refreshed at start-up, every 5 minutes and on *Check Home Assistant again* (`GET /api/admin/people?refresh=1`, which also re-runs the person sync). If HA can't be read, the last answer is kept; everything else only reads the cache, so nothing calls HA while holding a DB connection.
- **Extras.** Admin → People → 🔔 → *Also*: extra notify services for this app only (a picker of HA's `notify` services, or typed), up to 5 per person, stored in `user_notify`. `ha_notify.services_for(user)` = phones + extras, without duplicates; *Send a test* goes to both. Everything that decides who is notified or whether someone "can be notified" goes through it (`notifier.py`, `/me` and `/whoami` `notifyLinked`, the admin test).
- **Admin → People** is the shared people list (`common/people.js`; the routes use `app/common/people_admin.py`): one card per person with the Enabled / No access badge, the **Child** switch, **Enable** / **Disable** (with its confirmation), the phones as read-only chips (a warning chip when a phone has no notify action) or a hint ("No person linked", "No phone"), 🏠 home/away, and 🔔 opening the Notifications dialog (Phones, Also, Add, *Send a test*). `GET /api/admin/people` carries `ha {known, person, personName, phones: [{label, service, tracker}]}`, `presenceEntity` (effective) and `presenceChosen` (an admin's override).
- **Who gets a push for a new message.** Each member other than the sender, if all of these hold:
  1. They have access (not disabled) and a phone or an extra notify service.
  2. Their level allows it:
     - `all` → every message;
     - `direct_mentions` (the default) → direct chats, @mentions of them, @everyone in groups, and replies to their messages;
     - `off` → nothing.
     A chat's own setting (`all` / `mentions` / `off`) overrides the level; `muted_until` in the future means off. Announcements bypass mute and *mentions only* (§15.7).
  3. It isn't their quiet hours (quiet hours hold pushes back and send one summary when they end, unless they've read it meanwhile).
  4. They aren't already looking at that chat (a live connection that reported that chat visible and focused in the last 30 s, §8).
  5. It isn't their own personal room.
- **Batching.** The first message is sent at once. More from the same chat within 60 s are held and sent as one push: "Nisha: 3 new messages". A read marker cancels anything still held. Held pushes live in memory; a restart drops them.
- **Content.**
  - Title: the chat's name, or the sender's name in direct chats.
  - Message: according to the stricter of the App setting and the person's `notify_preview`:
    - `full`: "Nisha: Can you pick up milk?" (truncated to 200 characters; files as "📎 Lease.pdf");
    - `sender`: "New message from Nisha";
    - `none`: "New message in Household Chat".
  - Disappearing messages never show their text or file names (§15.8).
- **Opening the app.** `data` includes `url` / `clickAction` = the app's own page in Home Assistant (`config.INGRESS_URL`), so tapping opens Household Chat (not the exact chat). That page is the sidebar page **`/<full slug>`** — the full slug carries the repository's id (`/a1b2c3d4_household_chat`, `/local_household_chat`), and `/hassio/ingress/…` is older Home Assistant's address, a 404 now (APP_MESSAGES_SPEC §6.5). At start-up `ha_client.learn_page_blocking` asks the Supervisor (`GET /addons/self/info`: `slug`, `ingress_panel`, allowed without `hassio_api`); if that fails, the container's host name (the full slug with `-` for `_`). Not in the sidebar: `/app/<full slug>`. Outside Home Assistant: `/household_chat`. The log says which ("Notifications open … in Home Assistant").
- **Never** sends to disabled people or to people who have left. Unknown services are logged at start-up with a hint.

### 7.1 Delivery

`ha_notify.py` calls `POST /api/services/notify/<service>` through the Supervisor for each target, in a background thread after the database work is committed. Links travel in the message text only.

## 8. Live updates (`live.py`)

- **`GET /api/stream`.** Server-Sent Events, one per open tab, keyed to the user. The server sends a `: ping` every 20 s (ingress and proxies drop idle connections).
- **Events:** `hello`; `message` (with its id, for `Last-Event-ID`); `message_updated` (edits, deletes, reactions, pins, poll votes and closing); `message_removed` (§15.8); `read`; `typing`; `conversation` (`{id, removed?}`); `unread`; `presence`; `reminder`; `scheduled`; `settings`; `storage`; `call` (§15.12, only to the two people in the call); `closed` (disabled or restored) and `reload`. Each event carries only what that user may see, re-checked against membership at send time.
- **Hub.** An in-memory map of user → queues, with a bounded queue per connection (a slow client is dropped and reconnects). This works because the app runs one process: uvicorn with 1 worker, and the Dockerfile says so.
- **Resume.** The browser reconnects with `Last-Event-ID` (the newest message id it has); the server replays newer messages for the user's chats, up to 500, else tells the page to reload the chat. After reconnecting, the page also catches up through `GET …/messages?after=`.
- **Fallback.** If SSE fails three times, the page polls every 5 s while visible and every 60 s while hidden, and tries SSE again after a minute.
- **Presence.** The page posts `{conversationId, visible}` when it changes (for §7 rule 4) and a heartbeat every 25 s. "Online" = a live connection in the last minute; shown as a dot, and last seen as "5 min ago" (each person can hide theirs).

## 9. Frontend

- **Back gesture.** Android's back gesture (in the Home Assistant app or a browser) and the browser's Back close what's open in the app before leaving it: first a menu or dialog, then (on a phone) the open chat or page, back to the chat list. Only Back from the list goes to Home Assistant.
  - Everything open is a *layer* (`addLayer`/`removeLayer` in `core.js`), and while any is open there is exactly one extra history entry (`history.pushState`). Ingress pages are same-origin iframes, whose entries are part of the HA app's history, so its back gesture reaches them.
  - `popstate` closes the top layer and sets the guard again if more are open.
  - When the app itself closes the last layer (the ← button, a dialog's ✕), the guard is removed 300 ms later, unless a new layer opened meanwhile.
  - Desktop (wider than 760 px) keeps the list visible, so an open chat isn't a layer there; menus and dialogs are.
- **Layout.**
  - The page is a column: the first-run banner (§4.2, when shown) above the app, which fills the rest.
  - Desktop: a chat list on the left (search box, **＋** → *Direct message* / *New group*, ⋯ menu; **📌 My room** always at the top, then pinned chats, then newest activity; unread and @ badges; the "File storage isn't connected" banner; your name with the live dot and ⚙) and the open chat or page on the right.
  - Phone: one pane at a time, with a back arrow.
  - Themes Midnight (default, green accent) / Slate / Daylight / Auto, shared with the sibling apps (`common/themes.css`), applied before first paint by `common/theme-boot.js`; a saved **Vault** becomes Midnight.
  - Shared browser helpers: `common/ui.js` (`h()`, `api` via `UI.makeApi` with the app's `ApiError` and the 403 reload / 401 lock, `toast()`, `openModal()`, `confirmDialog()`), used by `core.js`.
- **Chat view.**
  - Header: name, members / online / home-away line, ⋯ with *Group info / Info*, *Files*, *Search in chat*, *Pinned messages*, shared folders (📂), *Notifications*, *Disappearing messages*, *Pin chat to the top*, *Download chat*, *Leave* and *Delete* (owner).
  - A pin bar with the newest pin, an announcements bar, a 🕓 bar for my scheduled messages.
  - Messages grouped by sender and minute, with date separators, an "Unread" divider, and a scroll-to-bottom button with a count. Older messages load when you reach the top (keeping the scroll position).
  - Long-press (phone) or hover ⋯ (desktop) on a message: *Reply*, *React*, *Copy text*, *Edit*, *Delete*, *Pin*, *Star*, *Remind me*, *Forward*, *Select messages*.
- **Composer.**
  - A growing textarea (Enter sends, Shift+Enter makes a new line; on phones, the send button). Right-click or hold ➤ for *Send later*.
  - ＋ menu: file or document, photo or camera, poll, disappear after…, send as announcement, send later, formatting help. @mention picker, emoji button, 🎤 voice.
  - Drop and paste files and images; upload progress per file, with cancel; *Send photos at original size*.
- **Files view.** A grid for images and a list for documents, filterable (All / Photos / PDFs / Voice / Other), with who and when; opens the viewer or downloads.
- **Viewer.** Images in a lightbox (arrow keys, swipe); PDFs in an iframe; text files as text.
- **Settings.** Notifications (level, preview, quiet hours with HA's time zone, whether a phone is linked, chats with their own setting), You (name and photo come from HA; hide online status), Look (theme), **🔓 Who can see your messages** (`privacyCard()`: members only in the app, admins can't read chats they aren't in; a warning notice that messages aren't encrypted, so anyone with the HA machine or its backups — including an admin who downloads the app's backup — can read every chat outside the app, My room included; chat downloads are readable by whoever has the file; no secrets, use Household Vault), and the version.
- **Privacy lines (§4.1 for everyone, not only DOCS).** The start page (no chat open), the My room banner and every chat's Info dialog carry one line saying the chat isn't encrypted and whoever can get into the HA machine or its backups can read it, with a link to Settings → Who can see your messages. My room's subline reads "Only you can see this in the app".
- **Admin** (⋯ → 🛡️ Admin, admins only): tabs People, Chats, Shared folders, App settings, Storage.
  - **Chats**: every chat's name, kind, members, last activity, message count and storage used (personal rooms appear as "Nisha's room", with size only). It doesn't open them; a note says "Admins can't read chats they aren't in". *Delete* appears only for a group with no enabled members.
  - An admin who hasn't enabled themselves gets *Enable me* and *Open Admin* on the "no access" page.
- **"How the app sees you"** (⋯ menu, and a button on the "no access" page; drawn by `HouseholdWhoami.panel()` from `common/whoami.js`): user name and id with copy buttons, display name "(not used for matching)", administrator or not, the number of names in `admin_users`, access, chats, phone linked; notices for "No admin yet" and display-name-only matches.
- **Accessibility.** The message list is a live region for new messages in the open chat; everything works by keyboard.
- **Layout** is checked at 360, 390, 768 and 1280 px.

## 10. Background jobs (`housekeeping.py`)

The 20-second loop is a `Jobs.every()` job of the shared runner (`app/common/housekeeping.py`) started in `main.py`'s lifespan and cancelled on shutdown; logging is set up by `housekeeping.setup_logging()`.

- **Every tick (20 s).** The disappearing-message expiry pass (§15.8), held and quiet-hour pushes, due reminders, due scheduled messages, poll closing, the app-messages outbox (`app_bus.run_outbox_once`: re-sends, expiry, pruning, the six-hourly `hello`; §15.11).
- **Every 2 seconds** (its own job, `calls.tick`). Calls (§15.12): rings that time out become missed calls, offers that never came are dropped, and an answered call ends when one side has been gone a minute.
- **Every minute.** Home/away from `GET /api/states` while someone has the app open (§15.3); typing states older than 6 s and expired presence are dropped.
- **Every 5 minutes.** Check the chat files folder (§5.3.1); refresh people and phones from HA (§7); scan shared folders (§12).
- **Every 10 minutes.** People's photos (§15.3.1).
- **Hourly.** Unsent uploads older than 24 h are removed.
- **Nightly (03:30 HA time).** Retention (§3.1); purge `_deleted` entries older than 30 days; mark missing files; remove orphan thumbnails; `PRAGMA optimize`. While the chat files folder isn't connected, the file jobs (purge, missing, orphan previews, stale parts) and the disappearing-file sweep skip it.
- Background jobs wait while a restore is running.

## 11. Invariants & pitfalls

- A chat's messages and files are only ever returned to its current members; being an app admin never bypasses this. Check membership on every route and at SSE send time, never only at connect. No admin route may return message text, file names or file contents from a chat the admin isn't in.
- File paths come from the DB, are resolved under the chat files folder and `realpath`-checked. Client-supplied names are display names only.
- No network call (HA notify, HA API) while holding a DB connection: collect recipients, commit, then send from a thread.
- Nothing is ever served inline that could run script (SVG, HTML, XML).
- One uvicorn worker (the SSE hub lives in memory).
- `ha_notify.py` and `ha_people.py` stay byte-identical across the household apps.
- Everyone starts disabled; disabling ends live connections at once. Nobody becomes an admin except through `admin_users`.
- Nothing is written into a chat files folder that isn't connected (§5.3.1).

## 12. Shared folders (`shared_folders.py`)

An admin shares an existing folder in Home Assistant's `/share` (e.g. `/share/Documents/House`) into **any chats** — groups, direct chats and personal rooms, their own "My room" too — several at once, each with its own access: read-only (`ro`) or read and write (`rw`).

- **Model.**
  - `folders (id, path UNIQUE, label, announce, created_by, created_at, last_scan_at)`: one row per `/share` folder (path relative to `/share`).
  - `folder_links (id, folder_id, conversation_id, mode ro|rw, created_by, created_at, UNIQUE(folder_id, conversation_id))`: one row per chat it's shared into.
  - `folder_files (folder_id, rel, size, mtime)`: the last scan.
  - At most 10 folders per chat. `/share` itself and the chat files folder (or anything inside or containing it) can't be shared.
- **Members** only ever use a **link id** (`/api/folders/{linkId}/list|file|search|upload|mkdir`): the link ties the folder to one chat, and you must be a member of it. The same folder through another chat's link is refused (404), admins included.
  - The chat detail and `GET /api/conversations/{id}/folders` list `{id: linkId, label, mode}`.
  - Uploads and new folders need `rw` on that link, and the right to post in that chat (a read-only direct chat can't write). A name that's already there gets " (2)".
  - Every path a member sends is resolved under the folder and `realpath`-checked; the mapping itself is re-checked on every use (a folder later swapped for a symlink is refused). Hidden files and symlinks out of the folder are skipped. Deleting files isn't offered.
- **Search.** `GET /api/folders/{linkId}/search?q=` looks through the whole shared folder, not just the open subfolder, for files and folders whose name contains every word typed; case and accents are ignored. The walk rules are the scan's: no hidden entries, no symlinks out, depth ≤ 8, at most 5 000 entries looked at. Up to 200 results come back as `{results: [{name, folder, dir, size, modified, mime}], truncated}`. The browser searches as you type (250 ms); a folder result opens it, a file result opens the viewer or downloads.
- **Several uploads** (rw links): *⬆ Upload files* takes many files at once, and on a computer you can drop files onto the dialog. They go one after another into the open folder, with a progress bar ("Uploading 2 of 5: …") and *Cancel*, and a summary at the end ("Uploaded 5 files", or the ones refused and why).
- **Announcements.** One scan per folder every 5 minutes. New files are announced in each linked chat when `announce` is on (not on the first scan), except personal rooms, which never get system messages; there, 📂 shows the files.
- **Admin routes.**
  - `GET /api/admin/share-dirs?path=` browses `/share` for the folder picker.
  - `GET /api/admin/folders` returns `{folders: [{id, path, label, announce, lastScanAt, exists, chats: [{linkId, id, name, mode}]}], chats: [{id, kind, name}]}`. `chats` lists every chat by name only: 📌 My room, 📌 X's room, 🏠/👥 groups, 💬 direct chats.
  - `POST /api/admin/folders {path, label?, announce?, chats: [{id, mode}]}` shares a folder. At least one chat is required, and a path already shared answers 409 (edit it instead).
  - `PATCH /api/admin/folders/{id} {label?, announce?, chats?}`: `chats` replaces the list, adding and removing links and changing access, with "shared the folder" / "was removed" notes in each chat. Chats it leaves hear the name they knew, new ones the new name. An empty list answers 422 (use Stop sharing).
  - `DELETE /api/admin/folders/{id}` stops sharing everywhere. Nothing on disk is touched.
- **Admin UI** (Admin → Shared folders): a card per folder (name, path, a chip per chat with its access, *Edit…*, *Stop sharing*). One dialog shares a folder (folder picker, name, announce, the chat list) and edits one; the chat list has a tick and an access choice per chat, plus a search box when there are more than 8 chats.

## 13. Tests (Python unittest)

`python3 -m unittest discover -s tests` with `requirements-dev.txt`. `tests/_env.py` is built on `common_tests/env.py`. `tests/base.py` runs every request through `TestClient(app, client=("127.0.0.1", 12345))`, captures notifications, runs background work inline, and replaces `ha_client.request` with a small `FakeHA` (`POST /template`, `GET /services`, `GET /states`, notify calls). The people in the fixtures are invented.

- **Access** (`test_access.py`): disabled by default; enable/disable; a disabled person's calls are 403 and their SSE is closed; membership 404 for others, removal hides at once; personal rooms (made on enable, one per person, visible only to the owner on every route, admins included, no members/leave/rename/delete, never notifies, own folder, kept over disable and re-enable); admins get 404 for chats they aren't in on every route; the admin overview has no content; admin delete only for a group with no enabled members; the ingress source check.
- **Messages** (`test_messages.py`): direct uniqueness, group roles, owner hand-over, system messages, paging, edit window, delete, FTS (only my chats), pins, polls, reminders, stars, forward, formatting limits, descriptions.
- **Files** (`test_files.py`): streaming limits, quota, blocked extensions, folder and file naming, rename moves the folder, path traversal and symlink refusal, serving headers, missing-file detection, photo resizing and EXIF removal, *Send original* byte-for-byte, voice.
- **Notifications** (`test_notify.py`): levels, per-chat override and mute, quiet hours, batching, "already looking", preview levels, never to disabled or removed people, notification replies (token, expiry, Mark as read), phones from HA (numbered `_2` too, extras without duplicates), the admin JSON, refresh and test send, home/away and photo following the login's person with the override winning.
- **Chat files folder** (`test_storage.py`): connected / not connected, adopting an existing folder, changing folders (new, confirm, refused), waiting deletes, restore keeps this install's folder.
- **Admin** (`test_admin.py`): settings validation, storage, backup and restore with and without files, retention, whoami; first run with no admins (the `noAdmin` flag, nobody promoted, the banner in the page).
- **Features** (`test_features.py`): child accounts, announcements, disappearing messages (per chat and per message, complete deletion, `secure_delete` and WAL checkpoint, backup leaves them out, restores expire before serving, `_disappearing/` files deleted without rows), send later, drafts, chat downloads, storage clean-up, photos from HA, shared folders (rules, browse/serve/upload/announce, search and several uploads, any chats with access per chat, 10 per chat, older databases migrated), and review regressions.
- **Shared** (`tests/common_tests/`, copies): `test_shared_copies.py` and the shared modules' own tests (whoami, auth_core, db_core, settings_core, people_admin, web_security, backup_core).
- **Packaging** (`test_packaging.py`, the shared checks from `common_tests/packaging_core.py`): `config.yaml` (version, url, ingress, `panel_admin: false`, no ports, options == schema == translations, empty defaults), every `?v=` equals the version, README/DOCS present, CHANGELOG.md whose newest (top) version heading equals the version, `spec/` present, icon and logo sizes, and no personal details in any text file of the app.

## 14. Defaults chosen

| # | Question | Decision |
|---|---|---|
| 1 | A default "Household" group | **Yes** — made with the first enabled person; everyone enabled joins; people can leave. |
| 2 | Do newcomers see earlier messages? | **Yes** by default. A group setting "New members see earlier messages" can turn it off (`joined_message_id`). |
| 3 | Blocked file types | **Refuse** the `blocked_extensions` list. |
| 4 | Online / last seen | **Shown**, with a per-person "hide my online status". |
| 5 | Can admins read everything? | **No.** Admins read only chats they're in, and see metadata for the rest (§4). |
| 6 | Chat names in `/share` folder names | **Yes**, as in §5.3. |
| 7 | A personal room for each person | **Yes** (§17). |

## 15. Features

### 15.1 Reply from the phone notification

- **What.** Notifications to a Companion app (a `notify.mobile_app_*` **action**) get two buttons:
  - **Reply** opens a text box on the lock screen, and the reply is posted as that person.
  - **Mark as read** moves their read marker.
- **How.**
  - Each notification carries `data.actions`: `[{action: "HCHAT_REPLY_<token>", title: "Reply", behavior: "textInput", textInputButtonTitle: "Send", textInputPlaceholder: "Message"}, {action: "HCHAT_READ_<token>", title: "Mark as read"}]`, and `data.tag = "hchat_<conversation id>"`, so a newer notification replaces the older one for that chat.
  - `<token>` is 128 random bits, stored in `notify_tokens(token, user_id, conversation_id, created_at)`, valid 24 hours, and usable any number of times within that.
  - The app keeps one WebSocket open to Home Assistant (`ws://supervisor/core/websocket` with `SUPERVISOR_TOKEN`; covered by `homeassistant_api`) in `ha_events.py`, built on the shared client `app/common/ha_ws.py`: one daemon thread "ha-events", reconnect after 5 s doubling to 5 min, a ping every 50 s, and a dropped connection is also noticed when pings go unanswered for about 190 s; `stop()` closes the socket and joins the thread. Connection messages are logged by the `ha_ws` logger. It subscribes to `mobile_app_notification_action` and ignores actions without its `HCHAT_` prefix.
  - On an action it looks the token up, checks that the person is still enabled and a member and that the reply isn't empty (≤ 2000 characters), then posts the message (`via = 'notification'`) or moves the read marker.
- **Limits.**
  - Only for notify **actions**: notify **entities** can't carry buttons, so those get plain notifications.
  - Replies travel through HA's event bus, visible to HA admins and automations (§4.1).
  - Anyone who could fire HA events *and* knew a token could post as that person. The tokens only exist in the notification payload and expire after 24 h.
  - App setting `notification_reply` turns the buttons off.

### 15.2 Pinned messages

- Any member (not children) can **pin** a message in a chat (up to 10 per chat) and unpin any pin. A system message records it ("Tarun pinned a message").
- The chat shows a pin bar under its header with the newest pin; tapping it goes to the message, and *Pinned messages* lists them all. A pinned message is marked 📌.
- Deleting a message removes its pin. Retention never deletes pinned messages.
- API: `PUT / DELETE /messages/{id}/pin`; `GET /conversations/{id}/pins`.

### 15.3 Home / away next to names

- **Which entity: automatically the person linked to their login** (`users.ha_person`, kept by the person sync), so it works with nothing assigned.
  - **Override:** Admin → People → 🏠 can pick a different `person.*` entity, or *Automatic — the person linked to their login*. The list shows every `person.*` entity with its friendly name and current state, their own Person first. Any `person.*` entity can be chosen, including a Person with no login (e.g. a child tracked only by a device).
  - Stored in `users.presence_entity` (NULL = automatic), validated as `^person\.[a-z0-9_]+$` and checked to exist when saved. Choosing their own Person stores NULL, so it keeps following the login. The effective entity is `COALESCE(presence_entity, ha_person)` (`presence.ENTITY_SQL`).
  - Admin → People shows the effective one: "🏠 nisha — from Home Assistant login" or "— chosen here".
  - An entity that later disappears from HA shows nothing and is flagged in Admin → People ("person.tarun no longer exists"). It's never replaced automatically.
- **What's shown** from the entity's state: `home` → 🏠 *Home*; `not_home` → *Away*; any other zone → 📍 *zone name*; `unknown` / `unavailable` → nothing.
- **Where:** a small line under the name in the chat list, the chat header, the member list and the people picker, with "since 14:05" from `last_changed`.
- **How.** One `GET /api/states` every 60 s (only while someone has the app open), mapped to each person's entity, and changes pushed as `presence` SSE events.
- **API.** `GET /admin/person-entities` → `[{entityId, name, state, linkedUserId}]`; `PUT /admin/people/{id}/presence {entity: "person.x" | null}`, admin only, recorded in `audit_log` (`presence_assigned`).
- **Privacy.** It's what HA already shows every HA user. App setting `show_presence` hides it for everyone.

### 15.3.1 Photos from Home Assistant

- **Source.** A person's photo is the `entity_picture` of their effective `person.*` entity (§15.3). Nobody uploads a photo in the app.
  - No entity, a person with no picture, or an entity deleted in HA → no photo (initials).
  - `show_presence` off hides home/away only; the photo still shows.
- **Copy, not a link.** `avatars.py` downloads the picture (≤ 8 MB) and stores a 256 px square JPEG (orientation applied, EXIF and location removed) as `/data/avatars/<user>.jpg`. Pages only ever load `GET /api/avatars/{id}?v=<avatar_version>` (enabled people only, sandboxed, cached a day), so nothing from HA is embedded and the CSP stays `img-src 'self'`.
  - `/api/…` pictures (uploaded in Settings → People) are fetched through the Supervisor with the app's token; `/local/…` pictures from `http://homeassistant:8123` without it; pictures on other sites are never fetched.
- **Kept in step.** Syncs run at start-up, right after an admin changes the entity, after a restore, and every 10 minutes (from the cached `GET /states`). The picture is downloaded again only when `entity_picture` changes (`users.avatar_src`); a change bumps `avatar_version`, removes the old copy if the picture is gone, and publishes `presence {refresh}` so open pages reload. If HA can't be reached, or a download or image fails, nothing changes and it's tried again next time.
- **Where it shows:** next to the person's name everywhere, as a direct chat's icon, and on **📌 My room** (your own picture; 📌 without one). Groups show their emoji or initials.
- **Older databases:** photos people uploaded themselves (`avatar_file` without `avatar_src`) and `/data/chat_photos` are removed at start-up; `chat_photos/` entries in older backups are ignored on restore.

### 15.4 Smaller phone photos

- **On upload**, a JPEG, PNG or WebP photo whose longest side is over `photo_max_px` (default 2000) is saved as a **resized JPEG** (quality 85): EXIF orientation applied, **EXIF GPS and other metadata removed**, the colour profile kept. An animated GIF or WebP is never resized; PNG screenshots under the size limit are left as they are. The original isn't stored.
- **Send original.** A checkbox per upload keeps the file byte-for-byte, metadata included. The UI says that originals can include the photo's location.
- The attachment records `original_size`, and the Files view shows "made smaller from 6.2 MB".

### 15.5 Polls

- 📊 in the composer: a question (≤ 200 characters) and 2–10 options (≤ 100 each); *Allow several answers*; optionally *Close on* a date and time. Not in personal rooms.
- **Voting.** Members tap to vote and tap again to change. Results show live as bars, with who voted for what (not anonymous). The creator (or a group admin) can **close** a poll early; after that nobody can vote. Deleting the poll message deletes the poll.
- A new poll notifies like a message ("Nisha started a poll: Biryani or pizza?"). Votes don't notify, except that the creator gets "Everyone has voted" once all members have.
- Schema: `messages.kind = 'poll'`; `polls(message_id PK, question, multi, closes_at, closed_at, closed_by, all_voted_sent)`; `poll_options(id, message_id, position, text)`; `poll_votes(message_id, option_id, user_id, created_at, PRIMARY KEY (option_id, user_id))`.
- API: `POST /conversations/{id}/polls`; `PUT /polls/{messageId}/vote {optionIds}`; `POST /polls/{messageId}/close`. Changes go out as `message_updated`.

### 15.6 Voice messages

- **Recording.** On a computer, click 🎤 to start and ➤ to send; on a phone, hold to record, release to send, and slide left to cancel. A timer and a level meter show while recording.
- **How.** Recorded in the browser with `MediaRecorder` (Opus in WebM on Chrome/Android/Firefox, AAC in MP4 on Safari), uploaded like any file (`Voice 2026-09-27 18.05.webm` in the chat's month folder) with `attachments.voice` and `duration`. The longest allowed is `voice_max_seconds` (default 5 min).
- **Playback.** An inline player: play/pause, a progress bar, the duration, and 1×/1.5×/2× speed. Voice messages count as "unheard" for the other members until played (`heard`, `POST /files/{id}/heard`).
- **Limits.** The microphone needs HTTPS (a secure context) and permission. Inside the HA phone app's web view it may be blocked, so the button explains why and suggests the browser.

### 15.7 Announcements

- Admins (or, with `who_can_announce`, group admins in their groups) can mark a message as an **announcement**, usually in the Household group ("Power cut tomorrow 10–2, charge your phones"). Not in personal rooms.
- **Look.** It shows highlighted in a bar at the top of the chat until every member has tapped **Got it** (or the sender takes it down), and lists who has and hasn't acknowledged it. Only members who could see it (joined before it) are counted.
- **Notifications.** An announcement is always sent, even to people who muted the chat or chose *mentions only*. It's never sent to someone whose level is *off*, and it respects quiet hours.
- **Reminder.** The sender can send one reminder to those who haven't acknowledged it.
- Schema: `messages.announcement`, `announcement_closed_at`, `announcement_reminded_at`; `announcement_acks(message_id, user_id, at)`.
- API: `POST /conversations/{id}/messages {announcement: true}`; `POST /messages/{id}/ack`; `POST /messages/{id}/announcement-reminder`; `POST /messages/{id}/announcement-close`.

### 15.8 Disappearing messages

- **Per chat.** A chat can be set to **disappearing messages**: *Off* / *1 hour* / *1 day* / *7 days* / *30 days*.
  - **Who sets it:** in a group, the owner or a group admin; in a direct chat, either person. Children can't change it (§16.5).
  - **What it covers:** every **new** message in that chat — text, files, voice, polls, pins, announcements — disappears that long after it was **sent**. Messages sent before it was turned on keep their own setting, and turning it off stops only new messages from disappearing.
  - **How it looks:** a system message (which itself stays) says "Meera turned on disappearing messages: 1 day". The chat shows ⏱ in its header and in the chat list, and the composer says "New messages disappear after 1 day".
- **Per message.** In any chat, ⏱ in the composer sets one message to disappear after 1 hour, 1 day, 7 days or 30 days (`expiresIn`; 0 = never, not allowed in a disappearing chat). Each disappearing message shows ⏱ and the time left.
- **When it expires** (checked every tick), it is **deleted completely**. There is no placeholder and nothing kept in `_deleted`:
  - the `messages` row is **deleted**, together with its reactions, poll, pin, stars, reminders, announcement acknowledgements, and its search (FTS) entry;
  - replies to it keep their own text, with the quote replaced by "Original message no longer available" (`reply_to` NULL, `reply_gone = 1`);
  - its attachment rows are deleted and its **files and thumbnails are removed** straight away (never moved to `_deleted`);
  - the chat's last-message preview and unread counts are recalculated, and a `message_removed` SSE event makes every open page drop it at once (pages also drop past-due messages themselves).
- **Leaving no copies in the app.**
  - `PRAGMA secure_delete = ON`, so deleted rows are overwritten with zeros.
  - After each expiry pass that deleted anything, the search index is merged (`optimize`) and a `wal_checkpoint(TRUNCATE)` runs, so old copies don't linger in the write-ahead log.
  - The app's **own backup** leaves out disappearing messages and their files, whether or not they've expired yet.
- **Notifications** never include the text or file names ("Nisha sent a disappearing message"), whatever the preview level. Reminders on them say "a message".
- **Restores.** Expiry is an absolute time stored with each message (`expires_at`), so a restore can't reset the clock.
  - **At start-up**, and straight after an admin restore, an expiry pass runs **before the app answers any request or opens the live stream**. Everything past its time is hard-deleted; messages that aren't due yet come back and disappear at their original time.
  - **Home Assistant backups** (full, or `/data` and `/share`) can bring back the database and files. Past-due rows are deleted at start-up, and their files are caught even without their rows: disappearing files are stored in the chat's `_disappearing/` folder, named `<expires_at as YYYYMMDDTHHMMZ>__<original name>`, and the expiry pass deletes any file there whose name says it's past due, whether or not the database knows it. A file there without a row and not yet due is left until its time.
- **Rules.** Disappearing messages can't be forwarded or included in chat downloads. They can be starred or given a reminder, but those go with the message. Admin clean-up (§15.10) can delete their files earlier, without seeing them. Deleting such a message or its group removes the files at once.
- **Honest limits (in the UI and DOCS).** Anyone in the chat could see, copy, screenshot or save it before it went, and so could anyone with access to the HA host or `/share` while it existed. Home Assistant's own backups taken before it expired still contain it until those backups are deleted. Deleting a file on an SD card or SSD doesn't guarantee the chip has physically erased it. It isn't a secure way to send passwords.
- **Schema.** `messages.expires_at` (+ index); `conversations.disappear_seconds` (NULL = off; 3600, 86400, 604800 or 2592000); every table that refers to `messages(id)` uses `ON DELETE CASCADE`, except `reply_to` (`ON DELETE SET NULL`).
- **API.** `PATCH /conversations/{id} {disappearSeconds}`; `POST /conversations/{id}/messages {expiresIn}`; SSE `message_removed`.

### 15.9 Download a chat

- **Download chat** (chat ⋯; members only, admins included only in their own chats): `GET /api/conversations/{id}/export?from=&to=` builds a zip:
  - `Chat name.html`: one self-contained, readable page with all messages, names, dates, pins, polls with results, and replies, with the same formatting;
  - `files/`: every file still available, with images shown in the page and other files linked relatively.
- Optionally limited to a date range (local days in HA's time zone).
- Disappearing messages (and quotes of them) are left out, and so are files over `export_max_mb` in total; the page says what was left out.
- **How.** Built in a temporary file under `/data`, streamed, then deleted. The HTML is generated with every value escaped, has no scripts and carries its own CSP (`default-src 'none'`). It's recorded in `audit_log` (`exported`).

### 15.10 Storage clean-up (admins)

- **Admin → Storage** shows the database size, the total used in the chat files folder per chat (bar list) and by type (photos, videos, audio, documents, other), missing files, the retention report (`retention {olderThan: {30, 90, 365}, settingDays, wouldRemove}`), and a warning when `share_folder_quota_gb` is 80 % used.
- **Largest files** (top 100) with chat, sender, date, type and size, filterable by type and "older than 6 months / 1 year". For chats the admin isn't in, a file shows only its type ("Photo", "PDF", "Voice message") and size — chat, sender, date and name are null — with no thumbnail, preview or download.
- Tick files and **Delete**: the file and thumbnail go to `_deleted` (purged after 30 days, or at once with *Empty now*); the attachment keeps its row with `admin_deleted_at`, and the message shows "File deleted by admin"; each deletion is in `audit_log`.
- Also *Delete thumbnails* (rebuilt on demand) and *Check files*.
- API: `GET /admin/storage/usage`, `GET /admin/storage/files?type=&olderThanDays=&limit=`, `POST /admin/storage/delete {attachmentIds}`, `POST /admin/storage/empty-deleted`, `POST /admin/storage/thumbs-delete`, `POST /admin/storage/check`.

### 15.11 Cards shared from other household apps (app messages)
Household Docs' *Send to chat* (Docs spec §17.15) reaches Chat as app messages over Home Assistant's event bus
(`APP_MESSAGES_SPEC.md`; kinds and checks in §6.3 there, link addressing in §6.5). `app_messages.py`:

- **One connection.** `ha_events.connection()` builds Chat's single WebSocket (notification buttons); the lifespan
  starts the bus on it (`app_bus.start(..., ws=…)`, `outbox_thread=False`) before starting it, and stops the bus
  before closing it. The outbox (re-sends, expiry, six-hourly `hello`) runs in the 20-second housekeeping tick.
  Without a Supervisor token (development, tests) the bus is off. `hello` says `can: ["chat.card",
  "chat.chats.list"]`.
- **`chat.chats.list`** answers the chats `requested_by` may post in — exactly `list_conversations()` (Chat's own
  order and visibility) without read-only ones — with names as that person sees them and member counts, trimmed to
  fit the 8 KB event (§6.3). *(security review 2026-10)* The list names no members (answers go into Home
  Assistant's event history); with `chat_id` it answers only that chat (one the person may post in, else `nack
  not_found chat`) with its enabled members' ids except theirs — Docs asks so only for the chat a person picked to
  give its members access.
- **`chat.card`** checks like a typed message (enabled user → current member → may post → fields — *(security review
  2026-10)* `panel` only the sending app's own page `/<1–16 of a–z 0–9>_<sender's slug>`, `target` only
  `/doc|folder|file/<id>` → the 30-a-minute
  limit) and posts **as `requested_by`**: a `messages` row of kind `card` (body = the title, so search and
  previews work) and an `app_cards` row (app, badge, type, item id, title, owner name, panel, target,
  shared-with-members). It follows the chat's disappearing setting; live events and notifications go out after
  the bus commits (`msg.after_commit(outbox.flush)`). During a restore every message is `nack busy`.
- **In the page** (`chat.js cardEl`): icon by type (📝 ✅ 🧮 📁 📄), "<person> shared **<title>**", "<Type> ·
  <owner>'s · from Docs", "Shared with this chat's members" when asked, and **Open in Docs** — a link to
  `panel + target` — *(security review 2026-10)* each card's **own** `panel`, checked again when shown
  (`chats.card_link(panel, target, app)`; before, the newest `panel` of any card was used for every card of that
  app, so one forged card could redirect them all) — opened by
  `common/connected-apps.js openAppPage()` (Home Assistant's `home-assistant/navigate` message, then its
  `location-changed` history navigation, then a plain top-level load). Without a known page: "Open the Docs app
  from the sidebar to see it."
- **Like messages:** unread, notifications ("Nisha shared a checklist “Trip 2026”" at the full preview level),
  search by title, reply quotes ("📄 <title>"), pins ("✅ <title>"), stars, reminders, reactions, delete (the
  sharer or a group owner/admin; removes the `app_cards` row), retention, chat downloads ("✅ <title> — shared
  from Docs (<owner>'s)", no link). **Not** editable, forwardable or copyable as text. A card never holds
  document content; whether a member may open the item is Household Docs' decision (its own "No access" page).
- **Admin → App settings → Connected apps:** a read-only card under the settings (`GET /api/admin/connected-apps`
  → `{on, connected, apps}`, drawn by `common/connected-apps.js`): name, version, last seen, what each app can do.
- **Privacy:** envelopes carry ids, names, titles and types only; DOCS.md shows the `recorder: exclude:
  event_types: [household_apps]` snippet (APP_MESSAGES_SPEC §8).
- **Tests:** `tests/test_app_messages.py` — every handler check (allowed and refused, nothing written on a nack,
  answer size), cards in search/notifications/exports/replies/delete/forward/edit, the migration of a database from before cards
  (data, column order, counter, indexes, triggers, FTS, cascades, a failure rolls back, an old backup restored,
  existing foreign-key problems don't block it) and end to end: the real lifespan on the fake event bus
  (`common_tests/fake_ha_bus.py`) with a fake Household Docs, one shared socket, duplicates acted on once; plus
  `common_tests/test_app_bus.py` on the app's own copies.

### 15.12 Voice calls (`calls.py`, `routers/calls.py`, `static/calls.js`)

Optional, **off** until an admin turns it on (App settings → *Voice calls*, `calls_enabled`). One-to-one calls in
direct chats, between two browsers on the **home network**. Calling from outside the home (STUN, a relay) and
the rest are planned in §18.1.

**What it does**

- A **📞** button in the header of a **direct chat** the person may post in. Not in groups, not in "My room" (§17;
  409 "Not possible in your personal room"), not in a read-only direct chat (§4: the other person has no access,
  or two children while `children_can_message_each_other` is off; 409). Children (§16.5) can call wherever they
  can post in a direct chat.
- **The caller** sees a full-screen call screen: photo, name, "Calling…" → "Ringing…" → "Connecting…" → the
  call's running time. A soft ringback tone plays while it rings. Once connected, both sides have:
  - **Mute** (the track is disabled; the other side is told over a small WebRTC data channel `hchat` that the
    caller opens, `{muted}`, and shows "Asha has muted their microphone").
  - **No Speaker button**: the outputs are picked under ⚙ instead (an earlier Speaker button guessed the
    loudspeaker by label and fell back to making the sound louder, which confused). The mute button is a drawn microphone (inline SVG) with a line across
    it when muted, since no emoji shows a muted microphone.
  - **⚙ Where the call plays**: shown only where the browser can pick outputs (`setSinkId`: the Home Assistant
    app on Android, computers; not iPhones). The outputs (`enumerateDevices`, without the `communications` alias,
    and without `default` when there are others) as buttons sorted and named by their labels — 🔊 Speakerphone
    (`speaker`), 📱 Earpiece (`earpiece|receiver|handset`), 🎧 Bluetooth headset
    (`bluetooth|bt|airpods|buds|headset|headphone|hands-free|sco`), 🎧 Wired headset (`wired|jack|usb`), else the
    label itself or "Output n". The choice is kept in the
    browser (`localStorage` `hchat.callOut`) for the next call. No microphone picker: the browser's default.
  - **Is sound getting through?** Every second the page reads the connection's statistics: `inbound-rtp`
    packets and `audioLevel` (their sound) and `media-source` `audioLevel` (my microphone), drawn as two level
    bars. After 5 s a line says what's wrong: no packets arriving from them, their sound silent for 6 s, they've
    muted, or my microphone silent for 6 s.
  - **Hang up**.
- **Playing the other person's sound**: phones and the Home Assistant app's web view only let sound start from a
  tap. The page keeps one `<audio>` element, started (with an empty stream) inside the tap on 📞 or Answer; the
  other person's stream goes into that same element when it arrives. If playing is still refused, a 🔈 button and
  a line ask for a tap. The ring tones' Web Audio context is closed once the call connects (some phones play
  WebRTC sound badly while one is open).
- **The person called** gets a ringing screen in every open Chat tab, in a browser or the Companion app (a ring
  tone made in the browser, and vibration where the phone allows it), and a phone notification (§7 phones and
  extras): title the caller's name, "📞 Asha is calling" (preview level *none*: title "Household Chat",
  "📞 Incoming call"), `tag: hchat_call_<call id>`, `ttl: 0`, `priority: high`, iOS
  `push.interruption-level: time-sensitive`, and two buttons:
  - **Answer** (`action: "URI"`, `uri` = the ingress URL) opens Chat. Notifications can't open a particular chat
    (§18, deep links), so the page asks `GET /api/calls/current` when it opens (and on every live-update
    `hello`, and on each poll while live updates are down) and shows the ringing screen above whatever page
    it's on.
  - **Decline** (`HCHAT_DECLINE_<token>`) is handled without opening anything, like *Mark as read* (§15.1): the
    token is a `notify_tokens` row for the person and the chat; the action declines that person's ringing call
    in that chat. It works even with `notification_reply` off.
  - Answering in one tab stops the ringing in the others ("Answered on another device") and takes the
    notification off the phone (`clear_notification` with the call's tag, sent to Companion-app services only —
    other services would show the words).
- **Unanswered** after *Ring for* (`calls_ring_seconds`, default 30 s): the call ends as **missed**.
- **Busy**: one call at a time per person. Starting a call while in one: 409 "You're already in a call."
  Calling someone who is in a call or being rung: 409 "Asha is on another call." (a busy tone), and a missed call
  (`busy`) is noted for them. Starting a call counts toward the 30 messages a minute (§4), since it can leave a
  note and a push.
- **Quiet hours and mute** (§7): the phone isn't rung when the person called is in their quiet hours, their level
  is `off`, or the chat is muted (`off` or `muted_until` in the future); open tabs still ring. The missed-call
  push then follows §7 like a message (held for the quiet-hours summary; none for a muted chat).
- **A call note** in the chat when it ends: a message of kind `call` from the caller, with an empty body, linked
  to its `calls` row. The page words it for each side: "📞 Outgoing call · 4 min" / "📞 Incoming call · 4 min",
  "📞 No answer" / "📞 Missed call", "📞 Busy" / "📞 Missed call", "📞 Declined" / "📞 You declined a call",
  "📞 Call couldn't connect", with **Call back** while calling is possible. Chat-list previews, exports and pushes
  use the neutral words ("📞 Call · 4 min", "📞 Missed call", "📞 Declined call").
  - Only **missed** (and busy) notes are unread for the person called (`unread_counts`), and only they are
    notified, as "📞 Missed call from Asha" (or "New message in Household Chat" at preview level *none*), through
    `notifier.new_message` with the chat's usual tag and buttons.
  - Notes follow the chat's disappearing setting and retention like other messages. They can't be edited or
    forwarded, and have no message menu. Deleting a note (by the API) removes its `calls` row.
  - A call that never rang (the offer never came) leaves no note.
- While a call is connected, the page holds a screen **wake lock** where the browser has one.

**How it works**

- **The sound goes browser to browser** with WebRTC (Opus, always encrypted between the two, DTLS-SRTP). The app
  never handles audio; it only passes the two browsers' descriptions and network candidates along.
- **Signalling over what Chat already has**: live-update `call` events (§8), sent only to the two people, and
  small POSTs:

  | Route | |
  |---|---|
  | POST `/api/calls {conversationId}` | Start → 201 `{id, iceServers, ringSeconds, peerId, peerName}`. 403 feature off; 404 not your chat; 409 personal room, not a direct chat, read-only, you're in a call, they're on another call. |
  | POST `/api/calls/{id}/offer {sdp}` | The caller's offer; the call starts ringing (its `calls` row is written). 409 unless the caller and the call is new. |
  | GET `/api/calls/current` | `{call: null}` or my call: `{id, conversationId, state, role, peerId, peerName, ringLeft, iceServers}`, plus `offer` and the caller's early `candidates` while it rings for me, and `answer` for the caller once answered. |
  | POST `/api/calls/{id}/answer {sdp}` | The person called answers. 409 unless they are, and it's ringing. |
  | POST `/api/calls/{id}/candidate {candidate}` | A network candidate found after the description went: passed on as an event, or kept (up to 50) while the other side can't take it yet. 409 from the person called before answering. |
  | POST `/api/calls/{id}/decline` | The person called declines while it rings. |
  | POST `/api/calls/{id}/end {reason?}` | Hang up. `reason`: `failed` (couldn't connect) or `no_microphone` (Answer couldn't use the microphone). |

  A description (`sdp`) is 1–16,000 characters, a candidate at most 1,000. The call routes answer 404 to anyone
  but the two people (as for chats), and need an enabled person like every route.
- **Events** (`call`, `{id, conversationId, state, …}`): `ringing` to the person called (`peerId`, `peerName`,
  `ringSeconds` — never the offer, which is fetched) and to the caller; `answered` to the caller (with `sdp` and the
  person called's early candidates) and to the person called (so their other tabs stop ringing); `candidate` to
  the other side; `ended` to both (`outcome`, `by`, and `reason` for `no_microphone`). A page ignores events for
  calls it isn't handling.
- **Fewer round trips**: each page waits up to 3 s for its network candidates before sending its offer or
  answer, so most calls connect with those two messages alone. That matters because Home Assistant Cloud and some
  proxies can delay the event stream. While a page's live updates are down (§8 fallback), it asks
  `GET /api/calls/current` every second during a call instead.
- **States**: `new` (POST `/calls`; dropped without a note if no offer comes within 20 s) → `ringing` → `active`
  (answered) → ended. A call answered but not connected within 30 s is ended by the page as `failed` ("Couldn't
  connect. For now calls work when both phones are on the home network."), as is a connection that fails.
- **Calls that are on live in memory** (`calls.py`), like the live-update hub (§8). This relies on uvicorn
  running with **one worker**, as the Dockerfile says; don't raise it. A job every 2 s (§10) ends rings that time
  out (missed), drops offers that never came, and ends an answered call when one side has had no live connection
  for a minute (`hub.is_online`). Disabling someone ends their call (`failed`). An admin restore drops the calls
  that are on. **A restart** ends them; at start-up (and after a restore) rows still open are closed as `failed`
  with their note (`close_unfinished`).
- **History**: `calls(id, conversation_id, caller_id, callee_id, started_at, answered_at, ended_at, outcome,
  message_id)`, `outcome` one of `answered`, `missed`, `declined`, `busy`, `failed` (NULL while on). Durations are
  `ended_at − answered_at`.
- **The microphone** needs a secure address (https, or `localhost`) and permission, asked when the person presses
  📞 or Answer. Ingress pages are same-origin iframes, so it works there as it does for voice messages (§15.6).
  Without it, 📞 explains why; *Answer* ends the call with `no_microphone` and explains (the caller sees "They
  couldn't answer here (no microphone)"). The Companion app's web view may block it.
- **No change to the CSP**: the page talks only to the app (`connect-src 'self'`), and WebRTC connections aren't
  covered by `connect-src`. The remote sound plays through an `<audio>` element's `srcObject` (not a URL).
- **ICE servers**: none in this release (`calls.ice_servers()` returns `[]`), so the browsers find each other on
  the home network only. §18.1 adds STUN and a relay.
- **Older databases**: `messages.kind`'s CHECK gains `'call'`. SQLite can't change a CHECK in place, so the
  messages table is rebuilt once at start-up exactly as for cards (§15.11; `db._allow_new_kinds`, which upgrades
  both 2.1 and 2.2 databases); the `calls` table is new.

**Tests**: `tests/test_calls.py` (who may call; ringing, answering, candidates, ending; the notes and unread
counts; missed calls and their pushes; the phone's Decline; busy; offers that never come; quiet hours and muted
chats; failures, disabling, the gone-side check and restarts; sizes; disappearing chats; a 2.2 database
upgraded) and `tests/test_calls_browser.py` (the app in uvicorn and two headless Chromium pages with fake
microphones, run with Chrome's strictest autoplay rule: ring, answer, sound both ways and both pages playing it, the level bars, mute (its icon) and the other side told, the output buttons, hang up, decline, the notes; skipped without Playwright).

## 16. More features

### 16.1 Remind me about this

- On any message: ⋯ → **Remind me**, then *In 1 hour* / *This evening (18:00)* / *Tomorrow 09:00* / *Next week* / *Pick a date and time* (`{preset: 1h|evening|tomorrow|nextweek}` worked out in HA's time zone, or `{at}`).
- **When it's due**, it sends a notification to **you only**: "⏰ Reminder: Nisha — Electricity bill due Friday" (following your preview level, and "a message" for disappearing messages). The message shows a ⏰ chip, and ⋯ → **Reminders** shows what's coming up and what's done.
- **Rules.** Private to you; up to 50 open reminders per person; reminders during quiet hours wait until they end; a reminder is dropped if you leave the chat, or the message is deleted or disappears.
- Schema: `reminders(id, user_id, message_id, due_at, done_at, created_at)`.
- API: `POST /messages/{id}/remind`, `DELETE /messages/{id}/remind`, `GET /me/reminders`, `DELETE /me/reminders/{id}`.

### 16.2 Starred messages

- ☆ on any message adds it to your own **Starred** list: private to you, across all chats, newest first, each with the chat name and a jump-to-message link.
- Stars disappear with the message (deleted or disappeared) or when you leave the chat.
- Schema: `stars(user_id, message_id, created_at, PRIMARY KEY (user_id, message_id))`.
- API: `PUT / DELETE /messages/{id}/star`, `GET /me/starred`.

### 16.3 Forward

- ⋯ → **Forward** on a message, or several selected (up to 20), then pick one or more chats you're in.
- **What gets sent.** Each is posted as a new message in the target chat, marked "↪ Forwarded", with no link back to the original chat. Files are **copied** into the target chat's folder, so deleting one doesn't break the other. Polls are forwarded as a text summary.
- **Rules.** Only from, and to, chats you're a member of; disappearing messages can't be forwarded; forwarding counts toward the rate limit; a child can only forward from a group to another child's direct chat if children may message each other.
- Schema: `messages.forwarded`.
- API: `POST /messages/forward {messageIds, conversationIds}`.

### 16.4 Simple formatting

- WhatsApp-style marks: `*bold*`, `_italic_`, `~strike~`, `` `code` `` and ```` ```code block``` ````; lines starting with `- ` or `1. ` as lists; `> ` as a quote.
- **How.** A small parser in the browser (`format.js`) turns the text into **tokens and then DOM nodes** (`<strong>`, `<em>`, `<s>`, `<code>`, `<pre>`, `<ul>`/`<ol>`, `<blockquote>`), never HTML strings. Links are auto-linked outside code, and mentions highlighted.
- The stored text stays exactly as typed; notifications and search use it as typed; chat downloads render the same formatting, escaped.
- ＋ → **Aa Formatting** shows the syntax.

### 16.5 Child accounts

- An admin marks a person as a **child** (Admin → People).
- **A child can:** read and write in the **Household group** and in chats an adult adds them to; start **direct chats with adults**.
- **A child can't:** create groups, add or remove people, rename chats, pin or announce; be a group admin or owner (becoming a child hands those roles to an adult); start direct chats with other children (unless `children_can_message_each_other` is on); delete other people's messages, send disappearing messages of their own, or change a chat's disappearing setting (in a disappearing chat their messages disappear like everyone's); forward out of a group to a direct chat with another child.
- Admins can't read a child's chats unless they're in them, like everyone (§4).
- The limits are enforced on the server (`is_manager()` is false for children); the UI just hides what isn't allowed.
- Schema: `users.is_child`. API: `PATCH /admin/people/{id} {isChild}`.

### 16.6 A description per chat

- Groups, direct chats (for both people) and personal rooms can have a **description** of up to 500 characters with the same formatting, shown under the chat name when you open it and in the chat's info.
- Group owners and group admins edit it in groups; either person in a direct chat. A system message says it changed (not in personal rooms).
- API: `PATCH /conversations/{id} {description}`.

### 16.7 Send later

- Long-press / right-click the send button (or ＋ → **Send later**), then pick a date and time (HA's time zone, at least 1 minute ahead, at most 1 year).
- **Before it's sent.** The message waits in **Scheduled** (a 🕓 bar in the chat and ⋯ → Scheduled, only visible to you): edit the text, change the time, *Send now* or *Delete*. Files are uploaded straight away and kept (`attachments.scheduled_id`), so they're never removed as unsent uploads.
- **When it's time** (the tick): it's posted as you, with the time it was actually sent, and notifies as usual. It's cancelled with a notice to you (`scheduled` event) if you're no longer a member (or are disabled, or can't post there) by then. A reply to a message that's gone is sent without the quote; a poll whose closing time has passed is sent open.
- Survives restarts (it's in the database). Up to 20 scheduled messages per person. Not in personal rooms.
- Polls and announcements can be scheduled too; disappearing timers start when it's sent. The app's backup leaves out scheduled messages that will disappear, with their files.
- Schema: `scheduled(id, user_id, conversation_id, payload JSON, send_at, created_at)`.
- API: `POST /conversations/{id}/scheduled`, `GET /me/scheduled`, `PATCH / DELETE /scheduled/{id}`, `POST /scheduled/{id}/send-now`.

### 16.8 Drafts kept across devices

- What you've typed but not sent in a chat is saved to the server 1.5 s after you stop typing, and at once when you leave the chat. It appears when you open that chat on another device, and the chat list shows "Draft: …".
- **Rules.** Private to you, up to 8000 characters, text only (attachments stay on the device where they were added). Cleared when you send (a save still waiting is cancelled) or empty the box. If two devices edit at once, the last save wins. Never in notifications, search or downloads.
- Schema: `drafts(user_id, conversation_id, body, updated_at, PRIMARY KEY (user_id, conversation_id))`.
- API: `PUT / DELETE /conversations/{id}/draft`; drafts are included in `GET /conversations` for the caller.

## 17. Personal room — "My room"

A private space for each person: notes to self, links, reminders and documents (a passport scan, a warranty, a shopping list) that **only they can see**. It looks and works like a chat with just you in it.

- **Who gets one.** Everyone who is enabled, made when an admin enables them (and at start-up for anyone enabled who lacks one). Exactly one per person (`kind = 'personal'`, `created_by` = the person, the person as the only member with role `owner`; `idx_one_personal`).
- **Who can see it.** Only its owner. Everyone else gets 404 on every route: messages, files, thumbnails, search, the live stream, pins, downloads, shared-folder links. **Admins are no exception**: the admin overview shows only "Nisha's room" and its size, and storage clean-up shows its files by type and size only (§15.10). The honest limit still applies: the text is in `/data/chat.db` and the files are in `<chat files folder>/<name> - personal (<id8>)/`, readable by anyone with the HA host, `/share` or backups (§4.1). The chat list says "Only you can see this".
- **What you can do in it.** Text with formatting, files and photos, the Files view and viewer, pinned messages, forward in and out (files are copied), edit, delete, search, starred messages, remind me, voice memos, a description, disappearing notes (per message), downloads, drafts, and shared folders an admin shares into it.
- **What it doesn't have.** No members to add, no leaving, renaming or deleting the room (you can delete messages), no polls, announcements, @mentions, typing indicator, "seen by", home/away, reactions, calls or send later (use *remind me*). Those routes return 409 "Not possible in your personal room".
- **Notifications.** Never sent for your own room; it has no unread count. Reminders you set (§16.1) notify as usual.
- **Limits and settings.** The same file size limit, blocked types and folder quota as other chats. `message_retention_days` **doesn't apply** unless `retention_includes_personal` is on, since these are things people chose to keep.
- **Disable and re-enable.** Disabling someone keeps their room and its files untouched but unreachable. Re-enabling gives it back as it was. The room is never given to anyone else.
- **In the UI.** Always the first entry in the chat list, as **📌 My room**, with the person's own Home Assistant picture (📌 without one).
- **API.** No routes of its own: it's a conversation with `kind: "personal"`, listed first by `GET /conversations`. `POST /conversations/direct` with your own id returns your room.

## 18. Possible future work

Ideas that are not built:

- **PDF first-page thumbnails** in the Files view — waiting for a small, pure-Python dependency.
- **Deep links** from a notification to the exact chat (the ingress URL would have to carry a route). Voice calls (§15.12) work around it by asking for a ringing call when the page opens.
- **Link previews**, if they can be made without fetching every shared link from the server.
- **Deleting files in shared folders** from the app.

### 18.1 Voice calls away from home, and later (not built)

Voice calls on the home network are built (§15.12). Nothing below exists yet; it is the plan.

**Release 2 — calls away from home** (small)

`calls.ice_servers()` starts returning STUN and relay addresses; the browsers already pass them to WebRTC.

1. **Away from home, direct**: each phone asks a **STUN** server for its public address and the phones try to
   connect directly. This works on many home and mobile networks. A public one such as Cloudflare's
   (`stun:stun.cloudflare.com:3478`, free, no account) can be entered. Only the phones' network addresses go to
   it, never audio or names.
2. **Away from home, relayed**: when the networks don't allow a direct link (common on mobile data and strict
   Wi-Fi), the audio needs a **TURN relay**. A Cloudflare Tunnel carries the signalling but *not* the call audio
   (tunnels don't pass UDP for public hostnames), so the relay is separate:
   - **Option A — Cloudflare Realtime TURN** (recommended with a Cloudflare setup): the admin creates a TURN key in
     their Cloudflare account and enters its *key id* and *API token*. For each call the server asks Cloudflare's
     TURN credentials API for credentials valid 4 hours (longer than any call) and gives them to the two phones
     in `iceServers`. The request goes straight from the app to Cloudflare (not through Home Assistant), with a
     5 s time-out and no DB connection held; if it fails, the call goes ahead without a relay and the failure is
     logged. Audio passes through Cloudflare still encrypted end to end. Cost: free up to a large monthly
     allowance (1,000 GB when this was written; check Cloudflare's pricing), and a voice call is about 30–60 MB
     an hour.
   - **Option B — your own TURN server** (e.g. a coturn app on Home Assistant): the admin enters its address and
     coturn's **shared secret** (`use-auth-secret` / `static-auth-secret`). The app makes short-lived
     credentials per call (user name `<expiry>:<call id>`, password = base64 HMAC-SHA1 of it with the secret), so
     no long-lived password is ever given to a phone. Needs a router port forwarded to it. Offer `turns:` on
     port 443 where it's set up, for networks that allow only web traffic.
   - **No relay set**: calls that can't connect directly end with "Couldn't connect from here — an admin can add a
     call relay in App settings".
3. **Test calling** in App settings checks the microphone, STUN and the relay with a short loop-back call in the
   admin's own browser.

New settings in the `calls` group (each `show_if` calls are on; the relay fields `show_if` their relay choice):

| Key | Default | |
|---|---|---|
| `calls_stun` | empty | e.g. `stun:stun.cloudflare.com:3478`; empty = home network only. Must start `stun:` or `stuns:`. |
| `calls_relay` | `none` | `none`, `cloudflare` or `turn`. |
| `calls_cf_key_id` | empty | Cloudflare TURN key id. |
| `calls_cf_api_token` | empty | **Secret** (write-only). |
| `calls_turn_url` | empty | e.g. `turn:home.example.com:3478` (`turn:` / `turns:`). |
| `calls_turn_secret` | empty | **Secret**: coturn's shared secret. |

These are Chat's first secret settings, so the backup routes (Admin → Storage) must start using the shared
`Registry.scrub_secrets` / `saved_secrets` / `keep_secrets` (`backup_core.blank_settings` / `saved_settings` /
`keep_settings`): a downloaded backup never carries the token or the secret, and a restore keeps the ones this
install has.

**Privacy (for DOCS)**: with a STUN server the phones' public network addresses go to it; with a relay the
encrypted audio passes through it. Names, messages and recordings never do.

**Tests**: Cloudflare credential requests against a fake server (never the real one in tests), coturn
credentials, `iceServers` in the call routes, secrets kept out of backups and kept over a restore, the settings
checks.

**Later** (small to medium each)

- **Video** (camera on/off, flip camera).
- **Group calls** of up to 4 people (each phone connects to each other; more would need a media server).
- A **Calls** list (recent, missed, call back).
- If another household app ever wants calls, the browser side (call screen, WebRTC set-up) would move to
  `common/static/` then, not before.

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass. (Live updates are a GET event stream; the app-bus messages don't use HTTP.)
