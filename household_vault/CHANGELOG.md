# Changelog

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
