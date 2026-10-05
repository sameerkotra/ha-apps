# Household Vault — design spec

The developer reference: how the app works today. The user guide is `DOCS.md`.

> **Experimental.** The app ships with `stage: experimental`. An independent security review is pending; until then users are told to keep their own KeePass copy (*Download all my passwords*, §12.6) and not to make the app their only copy (README, DOCS, the lock screen and Admin say so).

A household password manager as a Home Assistant app. Same family as Calorie Tracker, Household Todo, Splitpot, Household Chat and Family Tree (ingress auth, look and feel, admin and backup conventions — `HA_ADDON_PATTERNS.md` §4).

Every vault is a real **KeePass KDBX 4 file**, **encrypted at rest** with its own password. The **app's server does the encryption and decryption**: the files on disk, in HA backups and in downloads are always encrypted, and a vault is decrypted **in the app's memory only while someone who may open it is unlocked**. HA admins run the server and are trusted (§4).

## 1. Purpose & scope
- **For a small household** (about 4 people).
- **People.** Every HA user linked to a Person (Settings → People) is listed automatically, but **disabled by default**: nobody has any access until an admin **enables** them and **sets them up with a one-time master password** (§4.1). On their first unlock they must choose their own master password.
- **Vaults, one KeePass file each, each with one password.**
  - **Personal** — one per person, created at setup, locked with that person's **master password**.
  - **Household** — exactly one, shared automatically by every active person. Its password is random and nobody ever types it.
  - **Shared vaults** — any number, created by anyone (e.g. "Parents", "Finance", "Kids"). The creator (owner) chooses **who can use it** and its password:
    - **Same password** (a password the owner chooses and tells the others): everyone in it opens it with that **same password** — e.g. a vault a couple both use.
    - **Random password**: members never type it; it opens with their own master password (see *remembered*, below).
  - Any vault you own, **including your Personal vault**, can be shared with other people **with the same password** (§5.3).
- **One master password opens everything you've chosen.** Unlocking with your master password opens your Personal vault plus every vault whose password is **remembered** for you (Household, random-password vaults, and any same-password vault you ticked *Remember*). A vault you're a member of but haven't remembered shows 🔒 and asks for its password.
- **Folders.** Items live in **folders** (KeePass groups), nested as deep as you like, in any vault (§9.3).
- **Search.** Instant search across every open vault — titles, usernames, websites, tags, folder names and (optionally) notes (§9.4).
- **Item types:**
  - **Logins** — username, password, website, notes and tags, with a password generator.
  - **2FA codes (TOTP)** — live 6-digit codes, **stored in the same entry as the login** (no separate 2FA vault, by choice).
  - **Secure notes.**
  - **Cards** — payment cards, IDs, Wi-Fi and alarm codes, and so on.
- **KeePass-compatible.**
  - Any vault can be downloaded (still encrypted) and opened in KeePassXC, KeePassDX (Android) or Strongbox (iOS) with its password; *Show vault password* reveals a random-password vault's password to its members so they can do that.
  - Any existing `.kdbx` can be imported.
  - **Download all my passwords** (§12.6) builds one `.kdbx` with everything you can open, locked with your master password — handy for a phone, where KeePassDX or Strongbox can autofill from it.
- **Out of scope:**
  - Browser or phone autofill from the app itself, and any access outside the HA panel (use the combined download above, or the Vaultwarden app).
  - File attachments in the UI (attachments inside imported files are kept untouched).
  - Account recovery. **A forgotten master password can't be recovered** — the admin can only reset the person (their Personal vault is wiped). Shared vaults survive.
  - A separate vault or app for 2FA codes (a deliberate choice: the code lives with its login).
  - Copies of vault files outside the app's own `/data` (the app maps no other folder). The opt-in **personal copies** (§12.10) stay in `/data`.
  - HA sensors, except one **opt-in** sensor: the guest Wi-Fi (§12.9). Otherwise no vault contents are sent to HA. Notifications (§12.7) never contain secrets; expiry reminders name the item unless an admin turns that off.

## 2. Stack & file layout
- **Backend:** FastAPI + uvicorn on `python:3.12-alpine` (+ `tzdata`), port **8101**, pinned deps as in the sibling apps (`fastapi`, `uvicorn`, `python-multipart`, plus `cryptography` and `argon2-cffi`).
  - **KDBX written in-house** (`app/kdbx.py`) on `cryptography` + `argon2-cffi`, with a pure-Python Argon2 fallback (`app/argon2py.py`, checked against RFC 9106 vectors). No pykeepass (GPL) or lxml. Reads KDBX 3.1 (import only; Salsa20 in pure Python) and 4.x; writes 4.0 (4.1 when the source was 4.1). Unknown XML is kept. The KDF salt is kept between saves and the transformed key cached, so saves don't re-run Argon2.
  - **`cryptography`** for X25519 sealed boxes, HKDF and AES-GCM (the key ring, §5.2).
  - Python's `secrets` for passwords and passphrases (a hand-made 1,457-word list, `app/data/words.txt`).
  - TOTP done in a few lines with `hmac` (RFC 6238); no extra dependency.
- **Storage: SQLite only** (Python's built-in `sqlite3`, WAL mode, `/data/vault.db`) for metadata and sealed keys, plus the **encrypted** vault files. No separate database server.
- **Frontend:** plain HTML/CSS/JS, no build step, no third-party code. WebCrypto is used only for passkey quick unlock (§12.5). QR codes are read and drawn by `app/static/qr.js`, written for the app (§12.2).
```
household_vault/
├── config.yaml  Dockerfile  requirements.txt  requirements-dev.txt  .dockerignore  README.md  DOCS.md  icon.png  logo.png
├── translations/en.yaml   spec/SPEC.md   tests/ (tests/common_tests/: shared helpers and shared-module tests, copies)
└── app/  main.py config.py auth.py db.py storage.py sessions.py keyring.py kdbx.py argon2py.py items.py search.py
          totp.py passwords.py service.py health.py importers.py emergency.py alerts.py reminders.py guest_wifi.py
          qrcode.py copies.py settings.py ha_client.py
          common/ (shared Python, copies): ha_notify.py ha_people.py whoami.py ha_client.py housekeeping.py auth_core.py
                  db_core.py settings_core.py people_admin.py web_security.py backup_core.py
          routers/ me.py vaults.py items.py admin.py health.py sheet.py emergency.py guest.py quick.py common.py
          static/ index.html app.js qr.js style.css
                  common/ (shared browser files, copies): theme-boot.js themes.css ui.js settings.js settings.css
                          people.js backnav.js whoami.js http-warning.js
          data/ words.txt twofa_domains.txt
```
- **Shared code:** `app/common/`, `app/static/common/` and `tests/common_tests/` are copies of the repository's `common/` folder, written by `tools/sync_common.py` from `common/manifest.json` (see `common/README.md`). Never edit a copy (`tests/common_tests/test_shared_copies.py` fails if one was changed). `app/ha_client.py` is a thin module re-exporting the shared client plus the user sync; `auth.py` keeps Vault's sessions on top of the shared `auth_core`; `db.py` keeps its schema and `MIGRATIONS` on `db_core`. Vault's sessions, crypto and CSP stay its own (stricter on purpose).
- **Development:** `pip install -r requirements-dev.txt`, then `python -m unittest discover -s tests` from the app folder (the QR tests also need Node.js). `tests/test_packaging.py` checks the published package (the shared checks from `common_tests/packaging_core.py`): `config.yaml` (version, url, `stage: experimental`, ingress only, no `map`), cache-busting versions, docs (including `CHANGELOG.md`, whose newest (top) version heading must match `config.yaml`), icons, and that nothing personal is in any text file.

## 3. Manifest & options
- **Manifest:** `slug: household_vault`, `stage: experimental`, `url: https://github.com/sameerkotra/ha-apps`, `ingress: true`, `ingress_port: 8101`, **no `ports:`**, `panel_icon: mdi:shield-key`, `panel_admin: false`.
- **Permissions:** `homeassistant_api: true`, used to list HA users through their Person entities (`GET /api/states`, `person.*` with an `attributes.user_id`), the same way Splitpot does, to read each person's phones (`POST /api/template`, `GET /api/services`), to call `notify` (§12.7), and — only when someone publishes one — to keep the guest Wi-Fi sensor (§12.9). **No `map:`** — only `/data`, which the Supervisor always maps. `hassio_api`/`auth_api`/`docker_api`/`full_access` are false and `apparmor: true`.
- **Arches:** amd64, aarch64, armv7 (Argon2 on a Pi 3 is slow — see §5.1).

**Settings live in the app (Admin → App settings), not on the app's Configuration tab** (`HA_ADDON_PATTERNS.md` §4). The only app option is:

| Option | Default | Meaning |
|---|---|---|
| `admin_users` | `[]` | HA user ids or login names, never display names. Admins enable, set up, disable and reset users, handle backups and change App settings. Admins **can't open anyone's vault through the app** (they'd need its password), but as the people who run the server they are trusted (§4). |

**First run.** With `admin_users` empty nobody is an admin, so nobody can open Admin. `/api/me` and `/api/whoami` return `noAdmin: true` (and `/api/me` the caller's HA `username`), and every screen — lock screen, no-access, standalone pages, the unlocked shell — shows a banner: "No admin yet — add your Home Assistant user name (**<user name>**) to `admin_users` on the app's Configuration tab, save, and restart the app." with a link to *How the app sees you*. The first visitor is never promoted.

App settings (Admin → App settings, admins only; `app_settings` table; validated; apply without a restart). They're declared once in `settings.py` (`SETTINGS`, `GROUPS`: Security, Reminders and copies) on the shared `settings_core.Registry`:

| Setting | Default | Meaning |
|---|---|---|
| `clipboard_clear_seconds` | `30` | Overwrite the clipboard after this long (0 = never). The app can't check whether it still holds our value (§4.2 #7). |
| `min_master_password_length` | `12` | Also requires a strength score of "good" or better. Applies to master passwords and same-password vaults. |
| `allow_breach_check` | `false` | Lets users opt in to the breach check (§12.1); the app then contacts `api.pwnedpasswords.com`. |
| `session_max_hours` | `12` | Even when active, an unlocked session ends after this long (1–72). |
| `reminder_titles` | `true` | Whether expiry reminders keep and send the item's title (§12.8). |
| `personal_copies` | `false` | One `.kdbx` per person in `/data/copies` (§12.10). |

**Each person's own settings** (Settings): **Lock after** 1–60 minutes (default 5), stored in `users.auto_lock_minutes` and used on all their devices; lock when hidden (§12.13); search in notes (on/off); breach check (if allowed); download reminder (§12.6); security alerts and expiry reminders (§12.7).

**Notifications** (§12.7): each person's phones come from Home Assistant (Settings → People → Track device, the shared `app/common/ha_people.py`) while they're enabled and set up here; admins can add extra notify services in Admin → People → 🔔 (the shared `app/common/ha_notify.py` and `people_admin.py`).

## 4. Security model
- **Encrypted at rest, decrypted on the server while unlocked.**
  - Every vault file on disk, in HA backups, in the admin backup zip and in downloads is an encrypted KDBX 4 file.
  - Unlocking sends the master password (or a vault's password) to the app, which opens the file in memory. Decrypted vaults, derived keys and passwords are **held only in the app's memory**, only while at least one person allowed to open that vault is unlocked, and are dropped on lock, timeout, or app restart. They are never written to disk, logged, or sent to HA.
  - List and search responses **never include passwords or other protected fields**; a secret is sent to the browser only when someone taps *Copy*, 👁 or *Show* (§8).
- **Trust.** HA admins control the host and the app and are **trusted** by this household. What the design protects against: someone who gets a **backup, the disk, an SD card, a downloaded file**, or another app's files, and household members opening each other's vaults through the app. What it doesn't: someone with root on the HA host reading the app's memory while vaults are open, or changing its code. That's accepted (§4.2).
- **Users.**
  - **Sync.** The user list mirrors HA: every `person.*` linked to a user account is synced (name updates, new people added) at startup, and at most every 5 minutes when the Admin pages load. Nobody is ever deleted.
  - **New people are disabled.** Until an admin enables them they see only "Ask an admin to give you access" (and the whoami page); every other API call returns 403.
  - **Enable and set up** (Admin → Users): the server generates a **one-time master password** (a 4-word passphrase), creates the person's empty Personal vault with it, and shows it to the admin **once** (with *Copy* and *Print a slip*) to hand over in person. The person is `temporary`.
  - **First unlock** with the one-time password: the person must choose their own master password (strength rules apply; it must differ). The server re-encrypts Personal, **purges the older versions**, creates the person's **key-ring keypair** (§5.2), and sets `status = 'active'`. From then on the admin doesn't know anything that opens it.
  - **Only active people** can join Household, be added to shared vaults, or remember vault passwords.
  - **Disabled people** get a 403 page ("An admin has turned off Household Vault for you"); their sessions end at once; they're removed from every shared vault and Household, and their remembered passwords are deleted. Random-password vaults they were in are re-keyed (§5.4); owners of same-password vaults are asked to change the password.
  - **Re-enabling** an active person restores access to their Personal vault and puts them back in Household; not in other shared vaults.
  - Someone who opens the app without a matching Person is added (disabled) on first visit.
- **Identity.** Same as the siblings: requests are accepted only from the ingress proxy (`172.30.32.2`/loopback, `auth_core.INGRESS_HOSTS` in the shared `app/common/auth_core.py`) and identified by `X-Remote-User-Id`. Admins are matched by id or login name (`auth_core.is_admin`). The security headers (CSP, `nosniff`, `Referrer-Policy: no-referrer`, `no-store` on `/api`) are added at the end of the ingress guard by `web_security.SecurityHeaders.apply` (shared `app/common/web_security.py`), the same as before.
- **Sessions** (`sessions.py`).
  - Unlocking returns a random 256-bit **session token**, kept only in the page's memory (never in `localStorage`, cookies or `sessionStorage` — the origin is shared with HA and every other app) and sent as `X-Vault-Session`. It's bound to the HA user id: a token from someone else's request is refused.
  - A **page reload locks** (the token is gone). Idle timeout = the person's *Lock after*; hard limit `session_max_hours`; the browser also calls `POST /api/lock` when the tab has been hidden longer than *Lock after*, and on the 🔒 button.
  - A person can be unlocked on several devices at once; each has its own session. *Lock everywhere* in Settings ends them all.
- **Who can reach a vault.** A vault can be opened only by its members (Personal: the owner, plus anyone it's shared with). Everyone else gets 404, so existence isn't leaked. Being a member isn't enough on its own: you also need the vault's password — typed, or remembered in your key ring.
- **Network.** On plain `http://` (LAN without TLS) master passwords cross the network unencrypted. The app shows a warning banner on `http://` unless the host is `localhost` (drawn by the shared `common/http-warning.js` with Vault's wording), and DOCS.md recommends HTTPS (Nabu Casa, or a certificate).
- **Browser hardening** (still matters: ingress pages share HA's origin).
  - CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'`.
  - No inline scripts, no `innerHTML`/`insertAdjacentHTML` (DOM building only, `textContent` for user text), no third-party requests; site icons are initials, never fetched.
  - Every `/api` response is `Cache-Control: no-store`. Secrets revealed in the page are cleared from the DOM when hidden and on lock.
- **The server never logs** request bodies, passwords, entry contents or vault names; errors are logged with vault ids only.

### 4.1 Changing passwords
| Who / what | How | Needs |
|---|---|---|
| **An admin, first setup** | *Enable and set up*: one-time master password, changed by the person on first unlock (§4). | Admin rights |
| **You, your master password** | Settings → *Change master password*. The server re-encrypts Personal (fresh salt), re-wraps your key-ring private key, **purges Personal's older versions**, bumps `key_epoch`, and ends your other sessions ("Your master password was changed — unlock again"). If you shared your Personal vault **with the same password**, the people you shared it with are told to ask you for the new one. A reminder follows to download a new Emergency Kit and a new *Download all my passwords* file, and delete the old copies. | The **current** master password |
| **A same-password shared vault** | Owner → *Change vault password*: re-encrypted, older versions purged, the owner's remembered copy updated; every other member's remembered copy is deleted and they're told "The password of <vault> was changed — ask <owner>". | Owner, with the vault open |
| **A random-password vault** (Household and others) | *Re-key*: new random password, file re-encrypted, older versions purged, re-sealed to every member's key ring (§5.4). Automatic after someone is removed or disabled. | Any member (Household), owner/manager (others) |
| **An admin, someone else's master password** | **Not possible.** It isn't stored anywhere. | — |
| **An admin, someone who forgot theirs** | *Reset user*: wipes that person's Personal vault and key ring, removes them from every shared vault (re-keying random-password vaults; same-password owners are asked to change theirs), then *Enable and set up* again. Their own passwords are gone. | Admin rights. The app can't check that the person agreed; the confirmation says what is lost, and the person is told on their next unlock (and by notify, if assigned). |
| **A login stored in a vault** | Edit the entry; the old value stays in the entry's KeePass history. | Access to that vault |

HA backups made *before* a change still contain the old files, locked with the old password, until they rotate out.

### 4.2 Security risks & mitigations (summarised in DOCS.md)
| # | Risk | Mitigation / residual |
|---|---|---|
| 1 | **Anything else running as the HA web page** (a HACS card, custom panel, another app's XSS) could script into this page while it's unlocked and read what's shown or use the session token. | Strict CSP, no `innerHTML`, token only in memory, secrets fetched one at a time on demand, short auto-lock, lock on hide. **Residual:** can't be fully prevented from inside the page. Keep third-party frontend cards and panels to trusted ones. |
| 2 | **Root on the HA host** (a trusted admin, or an attacker who gets SSH/a malicious app) could read the app's memory while vaults are open, or change its code to capture passwords. | **Accepted**: admins are trusted. Protect HA admin/SSH access, keep apps to trusted sources. Vaults are only in memory while someone's unlocked. |
| 3 | **Weak master password** + a stolen backup, disk or download → offline guessing. | Argon2id (§5.1), minimum length 12 and a "good" score; suggest a 4–5 word passphrase. |
| 4 | **Forgotten master password** — permanent loss of that Personal vault. | Emergency Kit at setup; *Download all my passwords* reminders. Shared vaults survive. |
| 5 | **Someone gets your HA login.** They can reach the app but still need your master password to open anything. They *can* try passwords online. | 5 wrong passwords → 1 minute wait, doubling up to 1 hour, per user (server-side, `unlock_failures`). Version history. Turn on **HA multi-factor authentication**. |
| 6 | **Unlocked and unattended device.** | Auto-lock after the person's own time, lock on hide, lock button, header countdown, *Lock everywhere*. |
| 7 | **Clipboard.** Other apps, clipboard history (Win+V, phone keyboards) and cloud clipboard sync can read copied passwords. | The clipboard is overwritten after 30 s; the app can't check it still holds our value (reading needs a permission prompt, not allowed in Firefox or the HA app's web view), so the toast says "will be cleared". Prefer 👁 on shared screens. **Residual:** clipboard history keeps its own copy. |
| 8 | **A removed/disabled member** keeps anything they saw or downloaded. | Access is cut immediately; random-password vaults are re-keyed, same-password owners are prompted to change the password; the UI suggests changing important passwords they knew. |
| 9 | **Downloaded `.kdbx` files** on laptops and phones. | Only as strong as their password. Delete old copies after a password change. |
| 10 | **Admin actions** (disable, reset, restore a backup). | Every admin action is in `audit_log` and shown to the affected person on next unlock. |
| 11 | **Restoring an old backup** brings back removed members and old passwords. | After an import every user sees "A backup from <date> was restored" and the list of removals and password changes made after that date (kept in `restore_notes.json` outside the replaced DB), so they can be redone. |
| 12 | **Old copies after a password change** (HA backups). | Server copies are purged on change. **Residual:** earlier HA backups keep them until they rotate out. |
| 13 | **Sharing your Personal vault with the same password** means the other person knows your master password. | They still can't open your other vaults: they'd have to be signed in to HA as you. But anyone with your master password *and* a backup could open your key ring offline. The Share dialog says so and suggests a separate shared vault instead. |
| 14 | **Master password over plain HTTP.** | Warning banner on `http://` (`common/http-warning.js`); use HTTPS. |
| 15 | **Bugs in crypto handling.** | Well-known primitives only (`cryptography`, `argon2-cffi`); the KDBX format code is in-house and covered by round-trip tests; no new cryptography is invented. **An independent security review is pending** — hence `stage: experimental` and the advice to keep an own KeePass copy. |
| 16 | **Emergency access abused** — a contact asks while you're away. | You're told at once (banner + notify) and can deny during the waiting period; nothing is released while an admin has you disabled (you couldn't deny); the Emergency vault can't be shared or edited by hand; only marked items are in it. **Residual:** if you never look during the wait, they get the marked items — choose contacts and the wait accordingly. |
| 17 | **Quick unlock** turns "can unlock this phone" into "can unlock your vault on this phone". A script on HA's origin (#1) could also prompt for the passkey and recover the master password. | Needs the device's fingerprint/face/PIN (user verification); master password again every 14 days (a rule the page honours — the server can't tell a passkey unlock from a typed one if a script leaves out `quickUnlockId`); 5 failures remove the device; removed on every master-password change; an alert when a device is added. Remove devices you don't use. |
| 18 | **Guest Wi-Fi** password readable outside the vault (DB, the QR picture, HA history and backups). | Opt-in per Household *Wi-Fi item* only; the dialog says so; other members are told; follows the item and disappears with it; DOCS shows the recorder exclusion. Use a guest network. |
| 19 | **Reminder titles** readable in the DB and in notifications. | Only for items with a reminder; an admin can turn titles off (`reminder_titles`). |
| 20 | **Personal copies** in `/data/copies` (§12.10). | Only in the app's own storage and backups; off by default; each is locked with its owner's master password and holds only what they can open. **Residual:** file names show HA user names. |

## 5. Crypto & KeePass compatibility
### 5.1 Vault files
- **Format.** KDBX 4, **AES-256**, **Argon2id**.
  - Vaults with a **human-chosen password** (Personal, same-password shared vaults): 64 MiB, parallelism 2, iterations tuned once on the HA box so opening takes about 0.5–1 s, **never below 3 iterations**. (On a Pi 3 or slower the tuning may stop at the floor and take longer; that's shown in Admin → Storage.)
  - Vaults with a **random 32-byte password** (Household, random-password shared vaults, the Emergency vault): the lightest Argon2id settings KeePass apps accept (1 iteration, 1 MiB, parallelism 1) — a slow KDF adds nothing when the key is random, and unlocking several vaults stays fast.
  - Imported files keep their own KDF and cipher until the person chooses *Upgrade security settings*; KDBX 3 is upgraded to 4 on first save.
- **Never lose data.** Fields, attachments, icons, custom data and history written by other apps are kept untouched on save (round-trip tests with KeePassXC files). A key file used on import is dropped in favour of a password; the user is told.
- **Writes are atomic.** Each save writes a new version file (`/data/vaults/<vault_id>/<version>.kdbx`, write-then-rename) and then updates the DB; a crash leaves the previous version intact.

### 5.2 The key ring (how one master password opens several vaults)
- Each **active** person has an **X25519 keypair**. The public key is stored plainly (`users.public_key`); the private key is stored encrypted (`users.private_key_wrapped`, AES-256-GCM with a key from Argon2id of the master password with its own salt, same parameters as Personal). Both Argon2 runs at unlock happen in parallel.
- A **remembered** vault password is stored as a **sealed box** to the person's public key (`vault_keys`: ephemeral X25519 → HKDF-SHA256 with info `household-vault-key-v1` ‖ vault id → AES-256-GCM). The server can seal to anyone at any time (it only needs the public key), but can unseal only while that person is unlocked.
- **Unlock** = open Personal with the master password + unwrap the private key → unseal every remembered vault password → open those vaults.
- Changing the master password re-wraps the private key; the keypair itself doesn't change, so remembered passwords keep working.

### 5.3 Sharing a vault ("same password")
- **Share** is available on every vault you **own**, including your Personal vault (with the warning in §4.2 #13). Pick people (active users) and a role:
  - **Can edit** or **Can view** (read-only); the owner can also make someone a **manager** (can add/remove people).
- How the people you add open it depends on the vault's password mode:
  - **Same password** (Personal, and shared vaults created with a chosen password): they must type **the vault's password** the first time — you tell them. They can tick **Remember** so that from then on it opens with their own master password (the server seals it into their key ring).
  - **Random password**: the server seals it into their key ring straight away (you're unlocked, so it has the password). They never type anything.
- A person you add who **doesn't know the password can't open it**, even though they're a member; the server checks the password it's given against the file.
- **Leaving** or being **removed** deletes that person's remembered copy immediately. For same-password vaults the owner is asked to *Change vault password* (the removed person knows it); random-password vaults are re-keyed automatically (§5.4).
- A vault's owner can **transfer ownership** to another member. Personal vaults can't be transferred.

### 5.4 Household and re-keying
- **Household** is created (random password) when the first person becomes active. Whenever anyone is unlocked and Household is open, the server seals its password to every active person who doesn't have it yet — so newcomers get it without anyone doing anything.
- **Re-keying** a random-password vault (after a removal or disable): new random password → re-encrypt → purge older versions → re-seal to every remaining member → bump `key_epoch`, **in one DB transaction after the new file is written**. If the vault isn't open right now (nobody with access is unlocked), it's marked `rekey_pending` and done automatically the next time a member unlocks.
- **Purging older versions on every password change** means a restore can never bring back a file locked with a password someone removed still knows, and restores never need an old password.

### 5.5 Entry conventions (KeePassXC-compatible)
| Type | Stored as |
|---|---|
| Login | Standard `Title`, `UserName`, `Password`, `URL`, `Notes`, tags |
| TOTP | String field `otp` = `otpauth://totp/...` (the KeePassXC format), in the login's own entry, **and** KeePass 2.47+'s own fields: `TimeOtp-Secret-Base32` (protected), plus `TimeOtp-Length` / `TimeOtp-Period` / `TimeOtp-Algorithm` (`HMAC-SHA-256`/`-512`) only when they differ from KeePass's defaults (6, 30, HMAC-SHA-1). Read order: `otp`, then older `TOTP Seed` / `TOTP Settings`, then `TimeOtp-Secret-Base32` (a file made in KeePass). SHA1/SHA256/SHA512, 6–8 digits, 30 s default |
| Secure note | `Title` + `Notes`; tag `note` |
| Card / ID | Protected custom fields `Number`, `CVV`, `PIN`, plus `Cardholder` and `Expiry`; tag `card` |

- The type lives in entry CustomData `HouseholdVault.Type` (`login` / `note` / `card`), which other apps ignore. Favourites are the tag `favourite`.
- **Folders are KeePass groups** (nested), so they show as folders in KeePassXC too; the KeePass **Recycle Bin** is honoured.
- Entry history follows the file's own settings (default: last 10 versions of each entry).

## 6. Data model (SQLite at `/data/vault.db`; files at `/data/vaults/<vault_id>/<version>.kdbx`)
```sql
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT,
  ha_person TEXT,                  -- person.* entity it was synced from (NULL = first seen via ingress)
  disabled INTEGER NOT NULL DEFAULT 1,       -- disabled by default; an admin enables and sets up
  status TEXT NOT NULL DEFAULT 'none' CHECK (status IN ('none','temporary','active')),
  public_key TEXT,                 -- X25519, base64; NULL until active
  private_key_wrapped TEXT,        -- {salt, argon2 params, nonce, ciphertext}; NULL until active
  auto_lock_minutes INTEGER NOT NULL DEFAULT 5 CHECK (auto_lock_minutes BETWEEN 1 AND 60),
  search_notes INTEGER NOT NULL DEFAULT 0,
  unlock_failures INTEGER NOT NULL DEFAULT 0, unlock_blocked_until TEXT,
  created_at TEXT NOT NULL);
CREATE TABLE vaults (id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('personal','household','shared','emergency')),
  password_mode TEXT NOT NULL CHECK (password_mode IN ('chosen','random')),  -- personal: chosen (the master password)
  name TEXT NOT NULL,              -- "Personal" / "Household" / shared: 1–60 chars (not secret: shown in lists)
  owner_user_id TEXT REFERENCES users(id),
  rekey_pending INTEGER NOT NULL DEFAULT 0,
  key_epoch INTEGER NOT NULL DEFAULT 1,      -- +1 on every password change / re-key
  version INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0, sha256 TEXT,
  updated_at TEXT, updated_by TEXT REFERENCES users(id), created_at TEXT NOT NULL);
CREATE UNIQUE INDEX idx_one_personal ON vaults(owner_user_id) WHERE kind = 'personal';
CREATE UNIQUE INDEX idx_one_household ON vaults(kind) WHERE kind = 'household';
CREATE TABLE vault_members (vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK (role IN ('owner','manager','editor','viewer')),
  added_by TEXT REFERENCES users(id), added_at TEXT NOT NULL, PRIMARY KEY (vault_id, user_id));
CREATE TABLE vault_keys (vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,   -- remembered passwords
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sealed TEXT NOT NULL,            -- vault password sealed to the user's public key
  key_epoch INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (vault_id, user_id));
CREATE TABLE vault_versions (vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
  version INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, key_epoch INTEGER NOT NULL,
  created_by TEXT REFERENCES users(id), created_at TEXT NOT NULL, PRIMARY KEY (vault_id, version));
CREATE TABLE audit_log (id TEXT PRIMARY KEY, vault_id TEXT, user_id TEXT, action TEXT NOT NULL,  -- no contents, ever
  created_at TEXT NOT NULL);
  -- created|saved|restored|shared|unshared|left|rekeyed|password_changed|downloaded|downloaded_all|imported|deleted|
  -- user_enabled|user_setup|user_activated|user_disabled|user_reset|backup_restored|unlock_blocked
CREATE TABLE quick_unlock (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id TEXT NOT NULL, label TEXT NOT NULL, wrapped TEXT NOT NULL,   -- §12.5
  key_epoch INTEGER NOT NULL, created_at TEXT NOT NULL, last_used TEXT);
```
- **More tables and columns** (full definitions in `app/db.py`): tables `app_settings`, `user_notify`, `emergency_contacts`, `reminders`, `recent_items`, `guest_wifi`, `quick_unlock`, `user_copies` (§12); columns `users.last_seen`, `last_unlock`, `previous_unlock`, `breach_check`, `download_reminder_days`, `security_alerts`, `expiry_alerts`, `hide_lock_seconds`, `last_typed_unlock`, `master_epoch`, `audit_log.actor_id`, and `vaults.mirror_fp`. Columns that an older database lacks are added at start-up (`db.MIGRATIONS`), and again after a backup restore.
- **Version history.** The last **20** versions of each vault are kept; older ones are pruned after each save, and **all** older ones are purged on a password change or re-key (§5.4).
- **Limits.** 20 MB per file, 50 shared vaults, 20 members per vault, 5 000 entries per vault (search stays instant).

## 7. Open vaults, saving & concurrency
- **One copy in memory per open vault**, shared by every unlocked session that may open it (`kdbx.py`, a lock per vault). Two people editing Household at the same time edit the same in-memory database, so there's **no file merging** and no 409-and-merge dance.
- **A vault stays open** while at least one unlocked session has it open; when the last one locks or times out, it's closed and its password and database are dropped from memory.
- **Saving.** Each change (entry, folder, move) is applied to the in-memory database and saved as a new version at once (atomic write, §5.1). Saves are debounced to at most one per second per vault.
- **Two people editing the same item.** The editor sends the item's `modified` time it started from; if the item changed since, the server answers 409 and the UI shows "Alex changed this just now" with both versions side by side, to keep one or the other (the other stays in the entry's history).
- **Validation.** Everything is built by the server, so uploads only happen on **import** (KDBX signature, size limit, and it must open with the given password).
- **Restoring an older version** (owner/manager, or the Personal owner): the server opens the old version (same `key_epoch`, so the current password works) and saves it as a new version; members are told.

## 8. API (all under ingress; `me` = the caller; 404 when the caller can't see the vault; `X-Vault-Session` required where marked 🔑)
| Method & path | Who | Purpose |
|---|---|---|
| GET `/api/me` | anyone | `{id, name, username, isAdmin, noAdmin, disabled, status, autoLockMinutes, searchNotes, …}` plus the person's settings, `lastDownloadAll`, `lastMasterChange`, `notifyLinked`, `personalCopy`, `guestWifi` |
| PUT `/api/me/settings` | enabled | `{autoLockMinutes, searchNotes, breachCheck, downloadReminderDays, securityAlerts, expiryAlerts, hideLockSeconds}` (any subset) |
| GET `/api/whoami` | anyone, including disabled | The shared contract (`app/common/whoami.py`, `WHOAMI_PAGE_SPEC.md`) with extras Set up, Vaults you can open, Account status, Phone linked for alerts, plus the app's `status` and `disabled` (`vaultCount` is no longer a top-level field); counts only; `noAdmin` |
| POST `/api/unlock` | enabled | `{password, quickUnlockId?}` → opens Personal and every remembered vault; `{session, vaults:[…], mustChangePassword, notices, couldNotOpen, …}`. 403 wrong password (counts towards the per-user wait, §4.2 #5). |
| GET `/api/session` | 🔑 | Is this session still unlocked |
| POST `/api/lock` · `/api/lock-everywhere` | 🔑 | End this session · all of mine |
| POST `/api/me/activate` | 🔑 temporary | `{newPassword}` — first unlock: re-encrypt, purge, create key ring, status active |
| POST `/api/me/password` | 🔑 | `{current, new}` — change master password (§4.1) |
| POST `/api/strength` · `/api/generate` | 🔑 | Strength of a password · a new password `{kind: random\|passphrase, length, sets, words, separator, number}` and its strength |
| GET `/api/users` | 🔑 | Enabled, active people (for Share and emergency contacts): names and ids only |
| GET `/api/vaults` | 🔑 | Vaults I'm a member of: `{id, kind, name, role, passwordMode, open, remembered, owner, members[{id,name,role}], version, updatedAt, updatedBy, …}` |
| POST `/api/vaults/{id}/open` · `…/forget` | 🔑 member | `{password, remember}` — open a vault whose password isn't remembered · forget a remembered password |
| POST `/api/vaults` | 🔑 active | Create a shared vault `{name, passwordMode, password?}` (chosen passwords must meet the strength rules) |
| PATCH `/api/vaults/{id}` · POST `…/delete` | 🔑 owner/manager · owner | Rename · delete `{confirmName}`. Personal and Household can't be deleted or renamed. |
| POST `/api/vaults/{id}/members` · DELETE `…/members/{uid}` · POST `…/leave` | 🔑 owner/manager · member | Share `{userId, role}` (seals the password for random-password vaults) · remove · leave |
| POST `/api/vaults/{id}/transfer` | 🔑 owner | `{userId}` — new owner (not Personal) |
| POST `/api/vaults/{id}/password` · `…/rekey` | 🔑 owner · member/manager | Change a same-password vault's password · re-key a random one |
| GET `/api/vaults/{id}/password` | 🔑 member, random-password vaults | *Show vault password* (for KeePassXC); logged and alerted |
| GET / POST `/api/vaults/{id}/folders` · PATCH / DELETE `…/folders/{fid}` | 🔑 · editor | The folder tree `[{id, name, parentId, count}]` · create `{name, parentId}` · rename/move `{name?, parentId?}` · delete (contents to the Recycle Bin) |
| GET `/api/vaults/{id}/items?folder=&recursive=` · GET `/api/items?tag=` | 🔑 | Item summaries — **no secrets**: `{id, type, title, username, url, host, tags, favourite, folderPath, hasTotp, modified, expires, …}` |
| GET `/api/vaults/{id}/items/{xid}` · `…/history` | 🔑 | Item detail with protected fields **masked** (`{"set": true}`) · the entry's KeePass history (no secrets) |
| GET `/api/vaults/{id}/items/{xid}/secret?field=` | 🔑 | One protected field, on demand (copy/👁). Rate-limited to 60 a minute per person; never cached. |
| GET `/api/vaults/{id}/items/{xid}/totp` | 🔑 | `{code, period, remaining}` |
| POST `/api/vaults/{id}/items` · PATCH / DELETE `…/items/{xid}` | 🔑 editor | Create · update (with the `modified` it started from, 409 when stale) · delete (to the Recycle Bin) |
| POST `/api/vaults/{id}/items/move` | 🔑 editor of both | `{items, toVaultId, toFolderId, duplicate}` — move or copy, within a vault or to another |
| POST `/api/vaults/{id}/restore/{xid}` · `…/trash/empty` | 🔑 editor | Restore from the Recycle Bin · empty it |
| GET `/api/search?q=&vault=&folder=&type=&favourites=` · GET `/api/tags` · GET `/api/expiring?within=` | 🔑 | Search (§9.4) · tags · expiring items (§12.8); summaries only |
| GET `/api/vaults/{id}/versions` · POST `…/versions/{n}/restore` | 🔑 owner/manager (Personal: owner) | History · restore (§7) |
| GET `/api/vaults/{id}/download` | 🔑 member | The current encrypted file |
| POST `/api/me/download-all` | 🔑 active | `{password, includeDeleted}` (asked again) → the combined file (§12.6) |
| POST `/api/import` | 🔑 | Multipart `.kdbx` (+ password, optional key file) or CSV, `target` (`new` or a vault), `skipDuplicates` (§12.11) |
| GET `/api/password-health` · POST `…/breach-check` · POST `/api/breach/check` | 🔑 | §12.1 |
| POST `/api/totp/parse` | 🔑 | Classify scanned or pasted text (§12.2) |
| GET / PUT `/api/vaults/{id}/sheet` · `…/items/{xid}/sheet` | 🔑 | Household sheet (§12.3) |
| `/api/emergency…` · PUT `/api/vaults/{id}/items/{xid}/emergency` | 🔑 | Emergency access (§12.4) |
| `/api/me/quick-unlock…` · GET `/api/quick-unlock/options` | 🔑 active · enabled | Passkey quick unlock (§12.5) |
| PUT / DELETE `/api/vaults/{id}/items/{xid}/guest-wifi` · GET / DELETE `/api/guest-wifi` | 🔑 Household editor · active (GET works without unlocking) | Guest Wi-Fi (§12.9) |
| GET `/api/admin/users[?refresh=1]` · POST `/api/users/{id}/setup` · PATCH `/api/users/{id}` · POST `/api/users/{id}/reset` · `…/unblock` | admin | List (with `status` and phones from HA) · enable and set up (returns the one-time password **once**) · `{disabled}` · reset · clear the wrong-password wait |
| GET `/api/admin/notify-services` · POST / DELETE `/api/admin/users/{id}/notify[/{service}]` · POST `…/notify/test` | admin | Extra notify services (§12.7) |
| GET / PUT `/api/admin/settings` | admin | App settings: the shared payload `{values, defaults, meta, groups, …}` (`meta` replaces the old `labels`: label, help, group, kind, range, …) plus `kdf, version, personalCopies` and the session counts; PUT now answers the same as GET |
| GET `/api/admin-storage-download-db` · POST `/api/admin-storage-import-db` | admin | Encrypted-only backup and restore (§9.1) |
| GET `/api/restore-notes` | enabled | What the last restore undid (§4.2 #11) |

## 9. Frontend
### 9.1 Features shared with the other apps
- **Themes.** **Midnight** (dark, the default; green accent), **Slate**, **Daylight**, and **Auto**, shared with the sibling apps (`common/themes.css`, with Vault's accent and own colours in `style.css`), as CSS variables on `[data-theme]`, applied before first paint by the external `static/common/theme-boot.js` (this app's CSP forbids inline scripts). A saved **Vault** theme becomes Midnight. The `theme` and `sidebarCollapsed` keys in `localStorage` are shared with HA and the sibling apps (one origin); an unknown theme name falls back to the default. Nothing else is stored in `localStorage`.
- **"How the app sees you" page** (`WHOAMI_PAGE_SPEC.md`, drawn by `HouseholdWhoami.panel()` from `common/whoami.js`), from the name chip (👤 on phones): user name and id (copy buttons), display name ("not used for matching"), Administrator (Yes/No), Names in `admin_users` (a count), **Set up** (not set up / waiting for first unlock / active), **Vaults you can open** (a count), **Account status**. Works while locked and for disabled users. Never shows secrets, vault names or other users.
- **Admin export / import** (Admin → Backup and restore): a `household-vault-backup-YYYYmmdd-HHMMSS.zip` with a consistent `vault.db` snapshot (`db_core.snapshot`), every vault file and kept version, and the personal copies — **all encrypted**; the zip is still built in memory, now with `backup_core.write_zip` / `walk`. Import (member names and the app's own checks with `backup_core.check_members`, files put back with `backup_core.copy_out`; its own swap and restore notes are unchanged) validates the zip layout, `PRAGMA integrity_check`, the tables, every file's KDBX signature and paths, then replaces everything, runs the start-up migrations again, locks everyone, and shows the restore notes (§4.2 #11).

### 9.2 Getting in
- **Before access.** A disabled person sees "Ask an admin to give you access to Household Vault".
- **Admin setup.** Admin → People → *Enable and set up* → the one-time passphrase shown large, once, with *Copy* and *Print a slip*.
- **First unlock.** One-time password → "Choose your own master password" (twice, strength meter, the minimum length and "good" score) → **Emergency Kit** (printable: name, how to open Personal in KeePassXC, a *Download Personal.kdbx* button, a blank line to write the password on; never stored).
- **Unlock screen.** One master password field, plus **Unlock with fingerprint / face** where quick unlock is set up (§12.5). After too many wrong tries: "Try again in N minutes".
- **Vaults you haven't remembered** appear in the sidebar with 🔒; tapping one asks for its password and offers **Remember with my master password**.
- **Header:** lock countdown, 🔒 Lock, search box.

### 9.3 Vaults, folders and items
- **Sidebar:**
  - ⭐ **Favourites**, 🕘 **Recently used** (kept per person, §12.13), **All items**, 🗓 **Expiring**, 🩺 **Password health**, **Tags**.
  - Each open vault (Personal, Household, shared vaults, 🔒 ones last) with its **folder tree**: expand/collapse (remembered in memory for the session), item counts, a ⋯ menu per folder.
  - Trash (each vault's Recycle Bin), then type filters (Logins, 2FA, Notes, Cards).
- **Folders:**
  - **New folder** / **New subfolder** anywhere in a vault you can edit; **Rename**; **Move** (drag and drop on desktop, *Move to…* on phones, including into another folder of the same vault); **Delete** (an empty folder goes at once; a folder with items asks, and moves everything to the Recycle Bin — restorable).
  - A **breadcrumb** above the list (Household › Utilities › Electricity); new items are created in the folder you're in.
  - Imported KeePass files keep their groups as folders; folders you make show as groups in KeePassXC.
- **Item list:** initials icon, title, username, and (in *All items*, search and favourites) the vault › folder path. Sort by name or recently changed. Multi-select → *Move to folder…*, *Move/Copy to vault…*, *Delete*.
- **Item panel:** copy username, password or code ("Copied — the clipboard will be cleared in 30 s"); 👁 reveal; **Open site** (new tab, `rel="noopener noreferrer"`); live **TOTP** with a countdown ring; card numbers masked except the last 4; ⭐ favourite; **Move to…** folder or vault; **Copy to…** another vault; history of changes.
- **Editor:** type-specific fields; folder picker; **generator** (random 8–64 with character sets, or 3–10 words from the built-in list with a separator and optional number; strength meter); **TOTP** by pasting an `otpauth://` URI or base32 secret, or scanning a QR code (§12.2).
- **Sharing:** vault ⋯ → **Share…**: pick people and roles; for same-password vaults a note "Tell them the vault's password; they'll enter it once" (and for Personal the §4.2 #13 warning with *Create a shared vault instead*). **People with access** list, remove, transfer ownership, *Change vault password* / *Re-key*, *Show vault password* (random vaults), version history and restore, delete, **Leave** (for members).
- **Import & export:** import `.kdbx` as a new shared vault or merged into one you can edit; *Download .kdbx* of any vault you can open (encrypted); **Download all my passwords** (§12.6). No unencrypted export.
- **Settings:** quick unlock devices, **Lock after** (1–60 min) and lock when hidden, *Lock everywhere*, notifications, search in notes, breach check, *Change master password*, emergency access, *Download all my passwords* and its reminder, *Import passwords*, *Import 2FA codes*, *Emergency Kit*. What others did that affects you is listed after unlocking ("Since you last unlocked").
- **Admin page** (one page, admins only; also reachable from the lock screen as *Manage people*): an "Experimental" notice · **People** (the shared people list, `common/people.js`: every HA person, **disabled until an admin acts**; status "Not set up" / "Waiting for first unlock" / "Active"; *Enable and set up*; the Access switch, with a confirmation before turning it off; *Reset…* with a big warning; *Unblock*; 🔔 opens the Notifications dialog: phones and extra notify services, *Send a test*) · **App settings** (drawn by `common/settings.js`: one card per group, help, range and default under each setting, Save / Discard changes; with the tuned Argon2 parameters, open sessions and the version) · **Personal copies** · **Backup and restore**.

### 9.4 Search
- **Where:** the search box in the header (**/** or **Ctrl+K** focuses it), across **every open vault** by default; chips narrow it to **this vault**, **this folder (and subfolders)**, a **type**, or **favourites**. 🔒 vaults aren't searched until opened (the results say "2 vaults are locked — open them to search there").
- **What's searched:** title, username, website (full URL and host — "amazon" finds `www.amazon.in`), tags, folder names and path, custom field **names**, card holder and the last 4 digits of card numbers; **notes** only if the person turned *search in notes* on. **Never** passwords, TOTP secrets, CVVs, PINs or other protected fields.
- **How it matches:** case- and accent-insensitive; every word must match (AND); words match at the start of a word ("net" finds "Netflix", "Airtel Xstream net"); quoted phrases match exactly; `tag:bank`, `folder:utilities`, `vault:household`, `type:card` and `has:2fa` filters.
- **Ranking:** title starts with the query › title word › website host › username › tags/folder › notes; favourites and recently used get a boost; ties sorted by title.
- **Speed:** runs on the server over an in-memory index built when a vault is opened and updated on every change (dropped on lock). Results appear as you type (debounced 150 ms), at most 200, each showing vault › folder path; ↑/↓ and Enter open an item; Esc clears.
- The Recycle Bin is searched only when you're in Trash.

## 10. Invariants & pitfalls
- Vault files are always encrypted on disk, in backups and in downloads. Decrypted data, vault passwords and private keys live only in the app's memory while someone with access is unlocked; never logged, never on disk, never sent to HA.
- List, search and detail responses never contain protected fields; secrets are fetched one field at a time.
- The session token lives only in page memory; nothing vault-related goes in `localStorage`/`sessionStorage`/cookies (shared origin).
- Every password change or re-key purges the vault's older versions; removal from a random-password vault always re-keys; removal from a same-password vault always prompts the owner.
- A member without the vault's password (typed or remembered) can't open it.
- Save everything written by other KeePass apps; round-trip tests must show nothing is lost.
- Nobody has access until an admin enables and sets them up; the one-time password must be replaced on first unlock.
- DOM building only; strict CSP; no third-party code in the page.
- With `admin_users` empty, every screen shows the "No admin yet" banner; nobody is promoted automatically.

## 11. Build & test
- **Tests** (`unittest`, `python -m unittest discover -s tests`; the QR tests need Node.js; fixtures are invented data):
  - users: disabled by default, setup → temporary → activate, one-time password refused after activation, reset, disable ends sessions and removes access, the ingress source check;
  - sessions: token bound to the user, idle timeout from the person's setting, max age, lock everywhere, wrong-password back-off;
  - key ring: seal/unseal, remembered vaults open at unlock, master-password change keeps them working;
  - sharing: same-password vault needs the password even for members, *Remember*, roles (viewer can't edit), leave, remove → prompt / re-key, Personal sharing;
  - re-key and password change purge older versions; restore within an epoch;
  - folders and items: create, nest, rename, move, delete to the Recycle Bin, move/copy across vaults, 409 on a stale edit, secrets never in list/search/detail payloads (a test scans every response);
  - search: matching rules, filters, ranking, protected fields never matched, notes only when enabled;
  - TOTP against RFC 6238 vectors, both TOTP field conventions; KDBX round trips (unknown fields, attachments, history kept), KDBX 3.1 import, key files; Argon2 against RFC 9106 vectors;
  - QR: `qr.js` round trips at every version and level, and `qrcode.py` module-for-module against `qr.js`;
  - password health, breach check (faked responses), household sheet, Google Authenticator export, CSV imports and duplicates, expiry reminders, notifications and phones from HA (a fake HA), guest Wi-Fi, emergency access, quick unlock (server side), personal copies, backup/restore with restore notes, older databases gaining their missing columns;
  - packaging (`test_packaging.py`): `config.yaml`, versions, docs, icons, the "No admin yet" flag and banner, nothing personal in any text file;
  - shared (`tests/common_tests/`, copies): `test_shared_copies.py` and the shared modules' own tests (whoami, auth_core, db_core, settings_core, people_admin, web_security, backup_core); `tests/_env.py` is built on `common_tests/env.py` and the fake HA is `common_tests/fake_ha.py`.
- **Checked by hand:** the UI in Chromium (Playwright), including quick unlock with a virtual authenticator.
- **Still to check by hand:** downloads opened in KeePassXC/KeePassDX and a real KeePassXC file imported; the camera and passkeys inside the HA phone app; the breach check, notify and the guest Wi-Fi sensor on a real HA box.

## 12. Features on top of the core
All of them stay inside the model above: **SQLite only**, files always encrypted at rest, secrets in memory only while someone is unlocked.

### 12.1 Password health and the breach check
- **Password health** (`GET /api/password-health`, `health.py`): logins with a password in open vaults, not in the Trash. *Weak* = strength score 0–1; *reused* = same password on ≥ 2 items across the person's open vaults (compared as HMAC-SHA-256 with a per-process random key); *old* = the current password was set > 365 days ago (walking entry history back while the password is unchanged); *no2fa* (§12.12). Facts are cached per open vault until it changes. No password, hash or secret is returned. Search filters `is:weak`, `is:reused`, `is:old`, `is:breached`, `is:no2fa`.
- **Breach check** with *Have I Been Pwned* Pwned Passwords **range API** (k-anonymity): the server SHA-1-hashes the password and sends **only the first 5 hex characters** to `https://api.pwnedpasswords.com/range/<prefix>` with `Add-Padding: true` (padding lines with count 0 ignored), then compares the returned suffixes itself. The password and full hash never leave the app.
- **Two switches:** the App setting `allow_breach_check` (default off — it needs internet) and a per-person toggle `users.breach_check` (default off; only settable while allowed).
- **When:** on demand from Password health (`POST /api/password-health/breach-check`, a background thread per session: every login in the open vaults, 1 request per unique prefix, at most 5 per second, giving up after 3 consecutive network errors with "couldn't check"), and as a new password is typed in the editor (`POST /api/breach/check`, debounced).
- **Results** are kept in memory for the session only, with badges in the list.

### 12.2 Scan a 2FA QR code
- **Scan QR code** in the TOTP editor: the **camera** (secure contexts only), a **screenshot** (paste, drop or choose a file; read with `createImageBitmap`, so no CSP change), or **typed/pasted** text. `BarcodeDetector` is used first when the browser has it; otherwise `app/static/qr.js`, written for the app: an ISO 18004 encoder (byte mode, all versions and levels, mask by penalty) and decoder (adaptive + Otsu binarisation, finder/alignment detection, homography, Reed–Solomon with Berlekamp–Massey/Forney, numeric/alphanumeric/byte/Kanji/ECI), checked against OpenCV in both directions for versions 1–40. Decoding happens in the browser; only the resulting text is sent to the server.
- `POST /api/totp/parse` classifies the text: `otpauth://totp` (or a bare base32 key) fills the editor; `otpauth://hotp` is refused; `otpauth-migration://` (Google Authenticator export, protobuf) is decoded on the server into a preview where the person picks accounts, a vault and a folder, and each becomes a login. It adds `existing: "Title (Vault)"` when the same secret is already in an open vault.

### 12.3 Printable household sheet
- Entry CustomData `HouseholdVault.Sheet` = `1`, `HouseholdVault.SheetFields` = JSON list of field names, `HouseholdVault.SheetWifi` = `WPA`/`WEP`/`nopass` — inside the encrypted file. `PUT /api/vaults/{id}/items/{xid}/sheet` (edit role) and `GET /api/vaults/{id}/sheet` (rate-limited like secrets, audited as `printed_sheet`, alerted to the vault's other members), both only for Household and the caller's own Personal vault.
- *Print sheet* builds a print-only page: a title, the date, a "keep this somewhere safe" note, the chosen fields in a large layout, and an optional **Wi-Fi QR code** (`WIFI:T:…;S:…;P:…;;`, network name = UserName, else the title; escaped per the `WIFI:` format) drawn in the page. A confirmation first; nothing saved.
- **Never printable:** `Number`, `CVV`, `PIN`, `otp`, `TOTP Seed`, `TOTP Settings` and the `TimeOtp-*` fields.

### 12.4 Emergency access
Lets a trusted household member reach chosen items if something happens to you.
- **What's shared.** You mark Personal entries "Include in emergency access" (CustomData `HouseholdVault.Emergency = 1`, `PUT /api/vaults/{id}/items/{xid}/emergency`). The server keeps an **Emergency vault** (kind `emergency`, random password, owner = you) rebuilt from the marked entries (history stripped, folder paths kept) after every Personal save and at unlock, only when an HMAC of the marked entries (keyed with the Emergency vault's password) or the mirror's version changed (`vaults.mirror_fp`). It's hidden from its owner's lists and search, and `vault_access` refuses anything but viewing (no share, edit, restore, import; re-key by the owner only).
- **Trusted contacts.** One or more active people and a **waiting period** (1–30 days, default 7). The Emergency vault's password is sealed to each contact's public key but **not released** until the waiting period has passed after a request.
- **Flow:** the contact taps *Ask for access* → you're told at once (a banner with *Deny* / *Approve now* when you unlock, and notify) → if you don't **Deny** within the waiting period, `grant()` flips `requested → granted` with a conditional UPDATE (a denial that lands first wins), adds the contact as viewer and copies `sealed` into their `vault_keys`. Checked every minute, at unlock and when the page asks. Removing a contact deletes them; if they had access, the Emergency vault is re-keyed and re-sealed to the remaining contacts.
- **Rules:** while the owner is disabled, requests are on hold (they couldn't deny); a contact disabled or reset is removed; an owner reset deletes the Emergency vault; a re-key re-seals every contact's copy; every step is in `audit_log` and shown to both people. Items you didn't mark are never included.
- **Schema:**
```sql
CREATE TABLE emergency_contacts (owner_id TEXT NOT NULL REFERENCES users(id), contact_id TEXT NOT NULL REFERENCES users(id),
  emergency_vault_id TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE, wait_days INTEGER NOT NULL CHECK (wait_days BETWEEN 1 AND 30),
  sealed TEXT NOT NULL,                     -- the Emergency vault password sealed to the contact's public key
  key_epoch INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ready','requested','denied','granted')),
  requested_at TEXT, decided_at TEXT, created_at TEXT NOT NULL, PRIMARY KEY (owner_id, contact_id));
```
- **Routes:** `GET /api/emergency`, `POST /api/emergency/contacts`, `DELETE /api/emergency/contacts/{uid}`, `POST /api/emergency/contacts/{uid}/deny|approve` (owner); `POST /api/emergency/{ownerId}/request` (contact).

### 12.5 Quick unlock with a passkey
- **Why.** Typing a long master password on a phone every few minutes is the main reason people pick weak ones.
- **How.** Settings → *Quick unlock* registers a **passkey** (WebAuthn platform authenticator: fingerprint, face or device PIN, user verification required) with the **PRF extension**. `POST /api/me/quick-unlock/begin` → PRF salt, challenge, user handle; the browser runs HKDF-SHA-256 (info `quick-unlock-v1|<user id>`) over the PRF output into an AES-GCM-256 key (the user id as associated data) and encrypts the **master password**; `POST /api/me/quick-unlock {credentialId, label, wrapped:{iv,ct}, password}` stores only that ciphertext after checking the password against the open Personal vault. To unlock, `GET /api/quick-unlock/options` (no session) gives the credentials with salts and wrapped blobs, only within 14 days of a typed unlock; the browser asks for the passkey (`evalByCredential`), decrypts the master password and sends `POST /api/unlock {password, quickUnlockId}`. The server never sees the PRF output; WebAuthn signatures aren't verified (nothing on the server depends on them).
- **Table:** `quick_unlock(id, user_id, credential_id, label, prf_salt, wrapped, key_epoch, failures, …)`; `users.master_epoch` (+1 per master-password change, which deletes the rows) and `users.last_typed_unlock`.
- **Limits:** needs HTTPS and a browser with PRF support (Chrome/Edge and Android; Safari 18+); bound to the HA host name; at most 10 devices; deleted when the master password changes; the master password is required again after 14 days (a rule the page honours), and 5 failed quick unlocks delete the device. An alert goes out when a device is added.

### 12.6 Download all my passwords — one file
- **What.** Settings → *Download all my passwords* builds **one `.kdbx`** with every entry you have open — Personal, Household and each shared vault you're in (locked ones are named in `X-Locked-Vaults` and the toast) — **locked with your master password**, which is asked for again. Built in memory on the server and streamed straight to your device; no copy is kept.
- **Contents:** Personal's folders at the root; each other vault as a top-level folder named after it. Logins with their TOTP (both conventions), notes, cards, custom fields, tags, favourites, entry history and imported attachments. **Left out:** the Recycle Bins (*Include deleted items* adds them). Entry UUIDs are kept, so a newer download can be merged into an older copy in KeePassXC (*Database → Merge from database*).
- **Encryption:** KDBX 4, AES-256, Argon2id with Personal's settings; key = the master password only, so it opens in KeePassXC, KeePassDX and Strongbox — which can autofill from it on a phone.
- **Before downloading:** a confirmation that the file holds every password you can open and is a copy. Active people only; a security alert goes to you.
- **Keeping it fresh:** `/api/me` → `lastDownloadAll` (from `audit_log` action `downloaded_all`) and `lastMasterChange`; `users.download_reminder_days` (0/30/60/90, default 0) shows a banner after unlocking when the copy is older than the setting, missing, or older than the last master-password change. *Later* hides it until the next unlock.
- **Filename:** `household-vault-<first name>-YYYY-MM-DD.kdbx`.

### 12.7 Notifications
- **Who gets what:** `alerts.services_for(conn, row)` — the person's phones from Home Assistant (`ha_people.py`: one `POST /api/template` renders every `person.*` with its `user_id` and each tracked `mobile_app` device; a device's notify action is `mobile_app_<slugify(name)>` or HA's numbered `_2…`, checked against `GET /api/services`; none → shown, not used) **only while `disabled = 0` and `status = 'active'`**, plus admin-assigned extras in `user_notify(user_id, service)` (at most 5; an extra still gets the *admin* messages). HA's people are read at start-up, every 5 minutes (a background task next to housekeeping — the 20 s housekeeping loop is a `Jobs.every()` job of the shared runner, `app/common/housekeeping.py` — cancelled on shutdown) and on *Check Home Assistant again* (`GET /api/admin/users?refresh=1`, called before any DB connection is opened); if HA can't be read the last answer is kept.
- **Sending:** `alerts.send(conn, user_ids, message, kind)` looks services up with the caller's connection and delivers from a thread. Kinds `security` and `expiry` respect `users.security_alerts` / `users.expiry_alerts` (default on); `emergency` and `admin` always go.
- **Security alerts:** 3+ wrong passwords in a row (throttled to one per 15 min), master password changed, download-all, quick unlock added (to you); a shared vault downloaded, its password shown, the household sheet printed, a guest Wi-Fi published (to the vault's other members). **Admin:** disabled, reset, backup restored. Messages hold names, vault names and network names, never secrets.
- **Admin → People → 🔔:** *Phones — from Home Assistant* (read-only, `ha {known, person, personName, phones[{label, service, tracker}]}` in `GET /api/admin/users`; a phone with `service: null` is flagged "Companion app action not found"), hints when no person or phone is linked, *Also* (the extras), *Send a test* to both (409 explains a missing phone or that the person isn't set up yet). Routes: `GET /api/admin/notify-services`, `POST/DELETE /api/admin/users/{id}/notify[/{service}]`, `POST …/notify/test`.

### 12.8 Expiry dates, reminders and item forms
- Expiry = KeePass `Times/Expires` + `Times/ExpiryTime` (KeePassXC shows it); a card's `Expiry` MM/YY sets the last day of that month when no date is set. `HouseholdVault.RemindDays` (0/1/7/14/30/60/90, default 30). API fields `expires` (YYYY-MM-DD or ""), `remindDays`; summaries carry `expires`; `GET /api/expiring?within=` lists expired/expiring items in open vaults.
- `reminders(vault_id, item_id, title, expires_on, remind_days, stage)` is refreshed whenever a vault is opened or saved (not for Emergency vaults, not for items set to "don't remind"); a half-hourly pass sends "expires in N days" (stage 1) and "expires today / expired on …" (stage 2) to the vault's members; items long expired when first seen are silent; a new date resets the stage. `reminder_titles` (App setting, default on) decides whether the title is kept.
- **Forms** (`HouseholdVault.Template`: wifi, bank, insurance, vehicle, identity, licence, software, utility, membership) are presets over the three types: preset custom field names (some hidden), a label for the expiry date, an icon. Empty preset fields aren't saved.

### 12.9 Guest Wi-Fi on the dashboard (opt-in)
- A Household editor publishes a **Wi-Fi item** (form `wifi`): `guest_wifi` (one row: vault, item, ssid, password, security, show_password) and `sensor.household_vault_guest_wifi` via `POST /api/states` (state = SSID; `entity_picture` = `data:image/svg+xml` QR of `WIFI:T:…;S:…;P:…;;`; `password` attribute only with *show password*). `app/qrcode.py` is a Python port of the `qr.js` encoder (tests compare module-for-module). Re-posted every 5 minutes if HA lost it (states set this way don't survive an HA restart); deleted on unpublish.
- It follows the item on every Household save (new password → update; item trashed, no longer a Wi-Fi item, no network name → unpublished). `GET /api/guest-wifi` works without unlocking for active people (the app's Guest Wi-Fi page).

### 12.10 Personal copies in /data (opt-in)
- App setting `personal_copies` (default off). When on, `copies.py` keeps **one `.kdbx` per active person** at `/data/copies/<HA user name>.kdbx` (name cleaned; the user id is added if two clash), built like *Download all my passwords*: Personal at the root, each other vault as a folder, Recycle Bins left out, TOTP in both `otp` and `TimeOtp-*`, **locked with the person's master password** (Argon2id with Personal's settings).
- **Contents:** Personal plus every vault the person has **remembered** (in their key ring) — never a same-password vault they'd have to type a password for. A remembered vault that isn't open at that moment is listed as *missing* and the copy stays *stale*.
- **When it's written:** a debounced background job (10 s) after any save of a vault it holds (`service.save` → `copies.vault_changed`), after someone's key ring changes (`remember`/`forget`: joining, leaving, removal, re-keying), after a master-password change (the new password), and at unlock when the copy is stale or missing. The master password is only in memory while the person's Personal vault is open, so changes made while they're away mark the copy `stale`; it's rewritten at their next unlock.
- **Deleted:** when the person is disabled or reset, and all of them when the setting is turned off.
- **Where it's kept:** `/data` is in Home Assistant's backups of the app, and the admin *Download backup* zip carries `copies/*.kdbx` too (restored with it, then marked stale). Table `user_copies(user_id, file, built_at, vaults, missing, stale)`. `/api/me` → `personalCopy {file, builtAt, stale, vaults, missing}`; `/api/admin/settings` → `personalCopies {enabled, folder, people[]}`.
- **Security:** the file is exactly as strong as the person's Personal vault (same password and KDF) and holds nothing they can't already open; admins can't open it.

### 12.11 Imports: CSV and duplicates
- `POST /api/import` takes a `.kdbx` (with its password and optional key file; the key file is dropped in favour of a password and the person is told) or a CSV export; CSV columns are recognised by header (Chrome/Edge, Firefox, Bitwarden, LastPass, 1Password, Dashlane, Proton Pass, KeePassXC): name, username, password, URL, notes, TOTP, folder (→ subfolders), favourite; at most 5,000 rows. `skipDuplicates` (default true) leaves out items whose (folded name, folded username, website host, password) is already in one of the caller's open vaults or earlier in the file; empty folders left behind are removed. Imported and copied entries lose this household's emergency/sheet marks. After a CSV import the page reminds the person to delete the unencrypted file.

### 12.12 Password health: missing 2FA
- `app/data/twofa_domains.txt`: a small offline list of well-known sites that offer authenticator codes. A login whose website matches (domain or subdomain) and has no TOTP gets issue `no2fa` (tile, badge, `is:no2fa`).

### 12.13 Smaller things
- **Tags** in the sidebar (`GET /api/tags`, `GET /api/items?tag=`). **Recently used** is kept per person in `recent_items` (ids only, 30 kept).
- **Lock when hidden:** `users.hide_lock_seconds` (-1 = with *Lock after*, 0, 10, 60); the page locks on a timer while hidden.
- **Drag and drop** (HTML5, only with a fine pointer): items onto a sidebar folder, vault root or Trash (Ctrl/Option = copy; items from a view-only vault are always copied); folders onto folders of the same vault (never into themselves) or onto Trash. Uses the move/folder APIs. Shift-click range selection and *Select all*.
- **2FA codes in KeePass's own fields:** saving an item with a 2FA code also writes `TimeOtp-Secret-Base32` (+ length/period/algorithm when not the defaults), and removing the code removes them. `items.sync_keepass_otp()` runs on every save, on *Download all my passwords*, and once when an older vault file without them is opened (saved as a new version, no entry history). The `TimeOtp-*` names can't be used as custom fields.
- **Back gesture in the Home Assistant app** (`app/static/backnav.js`, shared by the household apps): Back closes the top layer (menu, dialog, the mobile sidebar, an open item — closing an item empties the detail pane and stops its 2FA timer), otherwise returns to the start page (*All items*), and only on the start page leaves Household Vault. Before unlocking, the unlock / no-access / choose-password screen is the start page (the admin, guest Wi-Fi and "how the app sees you" pages go back to it). One extra same-URL history entry is kept while away from the start page; the URL itself never changes, so no item id or secret reaches the history.
- **Narrow screens** (checked at 360, 412, 768 and 1280 px): tables become labelled cards, the item list never grows wider than the screen, long names wrap, the lock button is icon-only and the idle countdown hidden on very narrow screens.

## 13. Review status
- An independent security review is pending. The app is published with `stage: experimental` until then.
- Accepted and documented residual risks: the quick-unlock 14-day rule is enforced by the page (§4.2 #17), same-origin scripts (#1), reminder titles (#19), personal-copy file names (#20).

## Security notes (2026-10)

From the October 2026 security review (`SHARED_CODE_PLAN.md` §11):

- **Ingress source check**: uvicorn starts with proxy headers off (`--no-proxy-headers` in the Dockerfile CMD), so `request.client.host` is always the TCP peer; `tools/check_build.py` checks it.
- **Cross-site requests**: the guard middleware runs `web_security.refuse_cross_site` right after the ingress check — any method but GET/HEAD/OPTIONS whose `Sec-Fetch-Site` is `cross-site` or `same-site` gets 403 `{"detail": "Forbidden: cross-site request"}`; `same-origin`, `none` and a missing header pass.
