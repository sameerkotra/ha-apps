# Changelog

## 2.3.1

- **Retry, empty-page and confirm buttons look right again.**

## 2.3.0

- **Every view has its own address.** A reload (after unlocking) keeps you in the same vault, folder, Settings or Admin tab, and Back from a sub-folder goes to its parent folder instead of All items. The address only names views and vaults/folders, never an item.
- **Phone drawer**: tapping beside the menu closes it, Tags start folded, folder arrows are easier to hit, and the type filters (Logins, 2FA, Notes, Cards) are now chips above All items.
- **Bigger copy buttons** on phones, and tapping a value copies it — the clipboard is still cleared afterwards.
- **Lists**: the title shows how many items there are; sort by name (with A–Z headers), recently used or modified; the 2FA list shows each live code; Expiring is split into Expired, Within 30 days and Later; the Trash says when each item was deleted and has its own Restore.
- **New item on phones** is a round + button; "+ Folder" and "Empty Trash" moved under ⋯ next to the list title.
- **Items**: health chips say what to do ("Breached: Change password →" opens the editor with the generator), notes come right after the fields, and cards show their last 4 digits.
- **Editor**: Name comes first, Vault and Folder are grouped under "Where", and Save stays in view.
- **Unlocking**: when quick unlock is set up it's the main button; the forgotten-password note is under Help.
- **On a computer**, the empty item pane shows favourites, recently used, what's expiring and health issues.
- **Notices** after unlocking are one box ("3 things need you") that opens up and can be dismissed.
- **Settings** is grouped (Unlocking, Alerts, Search & breach, Your data, Emergency access) with an index at the top; imports sit apart from the download.
- **Admin** has tabs: People, App settings and Backup (restoring is in its own danger box).
- **Password health** lists breached passwords first, groups reused ones by the password they share, and has a compact breach-check card below the list.
- **Guest Wi-Fi**: **Show big** shows the QR code full screen.
- **Loading and errors**: pages show a spinner while they load, and an item that fails to load offers Retry.

## 2.2.2

- **Back goes to the page you came from.** The phone's back gesture (and the browser's Back) now steps back through the pages you opened in the app, instead of jumping straight to the start page; dialogs still close first, and Back on the start page still leaves the app.

## 2.2.1

- **New**: editing a login with a 2FA code shows the current code — hidden until you press 👁, then live with its countdown (👁 again hides it, 📋 copies it) — and a **Show key** button that loads the saved setup key into the 2FA box, hidden or shown as you choose. Saving without changing the key leaves the 2FA as it was.

## 2.2.0

Security review 2026-10 (the restore/backup trust question is documented under *Security, in short* and left as is for now).

- **Security**: the guest Wi-Fi is no longer sent to Home Assistant. There is no `sensor.household_vault_guest_wifi` any more — the network's password sat in Home Assistant's history, in every integration that exports states and in Home Assistant's backups, and stayed there after *Stop showing*. The app's own **📶 Guest Wi-Fi** page (no unlock needed) is where guests scan it; a sensor left behind by an older version is removed at start-up.
- **Security**: once an admin is set up, every Admin route needs their unlocked session, and setting someone up, resetting them, downloading a backup and restoring one ask for the admin's master password again (counted like unlock attempts). Every app, custom card and proxied site inside Home Assistant shares one origin with this app, so a request carrying only the admin's Home Assistant identity could come from any of them. Before the admin is set up, Manage people on the lock screen still works, so the first admin can set up themselves and others.
- **Security**: one-time passwords have six words (were five) and expire after 48 hours if unused — the person shows as *Not set up* again and is told.
- **Security**: the app runs as its own user inside its container, not as root; the automatic API documentation pages (`/docs`, `/openapi.json`) are off.
- **Security**: when an admin adds a notify service to someone's alerts, that person is told on the services they already had.
- **Fixed**: restoring an older version of a vault that was saved under an earlier password says so instead of failing.

## 2.1.7

- **Fixed**: the *Website* box in the item editor had a label but no field to type in. It's back, for every kind of item, with an **↗ Open** button next to it.

## 2.1.6

- The image is built on one fixed Alpine release, so a rebuild can't quietly move to the next one.
- **Security**: a damaged or crafted KeePass file can no longer make the app loop for ever while reading it — it's refused as damaged. Importing opens one file at a time, asks for at most 256 MiB of memory (was 1 GiB) and stops at 64 MB unpacked (was 256 MB); a failed import as a new vault leaves nothing behind.
- **Security**: if anything goes wrong half-way through a change — a save, a password change, a re-key — the vault stays exactly as it was, on disk and for everyone who has it open. Older versions are only removed once the change is safely stored, so a failure can no longer leave a vault nobody can open.
- **Security**: only an admin can re-key Household by hand.
- **Security**: while you're unlocked, the app keeps a key derived from your master password, not the password itself; the breach check works out its hashes only while it runs and stops when you lock.
- **Security**: a 2FA code with odd settings in an imported file no longer breaks saving that vault; a vault that would grow past 20 MB is refused with a message instead of an error.
- **Security**: request sizes are counted as they arrive (5 MB, 20 MB for an import, 300 MB for a restore), not just taken from what the browser says.
- **Security**: search words no longer appear in the app's log; the browser no longer offers to save your master password.
- **Security**: turning off item names in expiry reminders also blanks the names already kept.
- **Fixed**: re-keying a vault keeps its older versions (they're encrypted again with the new password), and versions are kept by day too: the last 20, plus one a day for 30 days. Deleting several items at once, and adding several 2FA codes from an authenticator export, is one save.
- **Fixed**: a shared vault whose owner is reset or turned off gets a new owner (a manager first).
- **Fixed**: a vault you remember is no longer forgotten when its file can't be read for a moment.
- **Fixed**: restoring a backup unpacks and checks everything first, so a bad backup changes nothing; big backups no longer have to fit in memory.
- **Fixed**: if your vaults lock themselves while you're editing, you can unlock on the spot and what you typed is saved; typing now counts as activity too. A copied password the browser couldn't clear is cleared when you come back to the page, and when you lock.
- **Fixed**: imported items without a proper id get one; search names a vault the way you see it ("Neha's Personal"); vault names with any characters show in the *Download all* message.
- **Fixed**: the guest Wi-Fi sensor is updated only once a change is saved, and put right if anything about it differs.
- **Changed**: passphrases are rated by the app's own word list: 5 words are now "fair", so a new master password needs 6 words (or 5 with a number).
- **Changed**: a new App setting keeps the activity log for a year by default (0 = for ever); at most 20 unlocked tabs or devices per person (the oldest locks).
- **Changed**: faster unlocking of old KeePass 3 files, fewer requests while you work, personal copies rewritten without re-running Argon2, and Argon2 tuned at start-up. Home Assistant now restarts the app if it stops answering.
- **Shared code**: request sizes are counted as they arrive, API answers are never cached by the browser, and the app's files load faster (cached until the next version).

## 2.1.5

- **Security**: importing a KeePass file whose encryption settings would take hours or more memory than the machine has (a damaged or crafted file) is refused with a message saying so — up to about ten times KeePassXC's own settings still open. A file that unpacks to more than 256 MB is refused too, and while a file is being opened the rest of the app keeps answering.
- **Security**: a vault left open on an item with a 2FA code now locks itself after your idle time — the code refreshing every 30 seconds no longer counts as you doing something.
- **Security**: wrong passwords count the same everywhere you type one — unlocking, opening a vault with its password, **Download all my passwords**, changing the master password and setting up quick unlock — and several typed at the same moment each count, so none gets past the wait after 5 wrong ones.
- **Security**: after an admin restores a backup, each person sees only what the restore undid for them (an admin still sees the whole list).
- **Shared code**: pages no longer use inline event handlers; saving App settings is safer, and backup restores say clearly when a file is too large.

## 2.1.4

- **Home Assistant's time zone, always**: "today" (reminders, expiry dates, file names) now follows Home Assistant's zone, read at start-up, asked again until Home Assistant answers and every six hours after; until then the zone the Supervisor gives every app (Home Assistant's own), never UTC.

## 2.1.3

- **Shared code**: a link or notification that opens a page inside the app no longer counts as pressing Back, so the next Back goes to the page you came from instead of closing the app.

## 2.1.2

- **Security**: your Personal vault can no longer be shared — it opens with your master password, so sharing it meant giving that password away. The Share dialog now offers to create a shared vault instead. People you shared it with before keep their access until you remove them.
- **Security**: the guest Wi-Fi password is shown as text (on the app's Guest Wi-Fi page and in the sensor) only when you ticked *show the password as text* when publishing it. The QR code still contains it — that's how phones join.

## 2.1.1

- **Security**: the app now ignores forwarded-address headers (`X-Forwarded-For`): only Home Assistant's ingress proxy itself can reach it, whatever a request claims. No visible change.
- **Security**: cross-site form posts are refused — a change sent to the app from a page on another website is turned away. The app's own pages and the Home Assistant app work as before.

## 2.1.0

- **Themes**: Midnight, Slate, Daylight and **Auto** (follows your device) — the same four in every household app. The Vault theme is now Midnight; your saved choice carries over.
- **App settings page redrawn**: one card per group, each setting with its range and default under it, problems shown at the field before saving, and Save / Discard with a count of unsaved changes. Every setting, default and limit is unchanged.
- **Admin → People**: one card per person instead of a table; the notify editor says **Send a test** and shows a result for each service.
- **How the app sees you** and the **No admin yet** banner use the same wording in every household app; the banner names the user name to add to `admin_users`.
- Under the hood: this app now shares its code for Home Assistant sign-in, people and notifications, settings, backups and the page helpers with the other household apps (one copy, kept in step), so fixes reach every app at once. Nothing was removed.

## 2.0.0

First public release, in the Household apps repository (https://github.com/sameerkotra/ha-apps).

- A household password manager inside Home Assistant: every vault is a standard KeePass (KDBX 4) file, encrypted with its own password
- Personal, Household and shared vaults; people sign in with their Home Assistant login, and admins can't open anyone's vaults
- Folders, instant search, favourites, tags, recently used and version history
- Logins with 2FA codes (scan the QR code), cards, secure notes and ready-made forms
- Password generator, password health and an opt-in breach check
- Download everything as one KeePass file; import KeePass files, CSV exports and 2FA codes from an authenticator app
- Emergency access, expiry reminders, phone alerts and quick unlock with a passkey
- A printable household sheet and an optional guest Wi-Fi QR code for your dashboard
