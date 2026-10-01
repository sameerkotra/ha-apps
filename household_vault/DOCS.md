# Household Vault

> **Experimental.** Household Vault is a password manager that hasn't had an
> independent security review yet. Use it alongside your own copy, not instead
> of one: every person should keep a KeePass file of their passwords
> (**Settings → Download all my passwords**, which opens in KeePassXC, KeePassDX
> or Strongbox) somewhere safe, and download a fresh one now and then. Don't
> make this app your only copy of anything you can't afford to lose.

A password manager for the household, inside Home Assistant. Every vault is a
standard KeePass file (KDBX 4) encrypted with its own password. The app opens
a vault in its memory only while someone who may use it is unlocked; on disk, in
backups and in downloads the files stay encrypted.

- Everyone signs in with their Home Assistant login — there's no separate
  account — but nobody gets in until an admin **enables and sets them up**.
- Each person has a **Personal** vault, everyone shares one **Household** vault,
  and anyone can make **shared vaults**.
- **One master password** opens everything you've chosen to remember.
- Admins manage people and backups. **Admins can never open anyone's vaults.**

## Getting started

1. In Home Assistant: **Settings → Apps → Install app → ⋮ (top right) →
   Repositories**, paste `https://github.com/sameerkotra/ha-apps` and tap
   **Add**. Then, in **Settings → Apps → Install app**, find **Household Vault**
   and **Install** it. (The first install builds the app and takes a few
   minutes.)
2. **Start** it on the **Information** tab (turn on **Show in sidebar** there
   too). Open **Vault** in the sidebar: a banner says **No admin yet** and shows
   your Home Assistant **user name**.
3. On the app's **Configuration** tab, add that user name (or your user id —
   never the display name) to **admin_users**, **Save**, and restart the app
   (Information tab → **Restart**). The **How the app sees you** page (from the
   banner, or 👤 your name in the sidebar) shows the exact user name and id to
   use.
4. Open **Vault** again. As an admin you'll see **🛡 Manage people**.
   **Admin → People** lists everyone with a Home Assistant login, with **no
   access** until you tap **Enable and set up** — start with yourself. You get a
   **one-time password** (shown once; copy it or print a slip).
5. Unlock with the one-time password and **choose your own master password**
   (at least 12 characters and a "good" strength score; a 4–5 word passphrase
   works well). Nobody else will know it. Print the **Emergency Kit** you're
   shown.
6. Set up the rest of the household the same way and hand each person their
   one-time password in person.

The first person to finish setting up creates the **Household** vault; everyone
set up after that joins it automatically (the next time someone who has it is
unlocked).

**Use HTTPS** (Nabu Casa or your own certificate). On plain `http://` your master
password crosses your network unencrypted; the app shows a warning.

**A forgotten master password can't be recovered.** An admin can **Reset** the
person: their Personal vault is wiped, they're taken out of shared vaults, and
they're set up again. Household and shared vaults are kept.

## Unlocking and locking

- **Unlock** with your master password. It opens your Personal vault and every
  vault you've remembered. After 5 wrong master passwords you wait a minute,
  doubling up to an hour; an admin can **Unblock** you.
- The vault locks after **Lock after** minutes without activity (your own
  setting, 1–60 minutes, used on all your devices; the countdown is in the
  header), when you reload the page, with 🔒 **Lock**, and after the
  **Longest unlocked session** an admin set, even while you're active.
- **When I switch to another tab or app** (Settings → Locking) can lock at once,
  after 10 seconds or after a minute instead of waiting for *Lock after*.
- **Lock everywhere** (Settings) locks all your devices.
- **Quick unlock** (Settings → Quick unlock): unlock with your **fingerprint,
  face or device PIN** instead of typing your master password. It uses a passkey
  on that device; your master password is encrypted there with a key only the
  passkey can produce, and the app stores just the encrypted copy. You still
  type your master password at least every **14 days**; 5 failed quick unlocks
  remove that device; changing your master password removes all of them; up to
  10 devices. It needs HTTPS and a browser with passkey "PRF" support (Chrome or
  Edge on a computer or Android, Safari 18 or newer); it may not work inside the
  Home Assistant phone app — use the browser.
- After unlocking, **Since you last unlocked** lists what others did that affects
  you: you were given or lost access to a vault, a vault's password changed, an
  admin reset you or restored a backup, emergency-access events.
- In the Home Assistant phone app, **Back** closes an open dialog, menu or item
  first, then returns to *All items*; only Back on that start page leaves
  Household Vault. While locked, the lock screen is the start page.

## Vaults

- **Personal** — yours. It opens with your master password.
- **Household** — everyone who's set up. It has a random password nobody types.
- **Shared vaults** — ＋ next to *Vaults*. Choose how it's locked:
  - **Same password**: you choose a password and tell the others (for example a
    couple sharing one vault). Each person types it once and can tick
    **Remember**, so it then opens with their own master password.
  - **Random password**: it opens automatically for everyone you add.
- **Share…** (vault ⋯ menu): pick a person and *Can edit*, *Can view* or
  *Manager* (can also add and remove people). Only people who are set up can be
  added. **People with access** shows who's in it.
- You can share **your Personal vault with the same password** — they'll open it
  with your master password, so they'll know it. They still can't open your
  other vaults (they'd have to sign in to Home Assistant as you), but a separate
  shared vault keeps your master password private, and the Share dialog suggests
  one.
- A vault you're in but haven't remembered shows 🔒 in the sidebar; tap it and
  type its password (tick *Remember* to open it with your master password from
  then on).
- **Removing someone**: a random-password vault gets a new password
  immediately; for a same-password vault you're reminded to **Change vault
  password** (they know the old one). Either way, change important passwords
  they could have seen.
- More in the vault ⋯ menu: **Rename**, **Leave**, **Transfer ownership** (not
  Personal), **Delete** (type its name to confirm; Personal and Household can't
  be deleted), **Change vault password** (same-password vaults), **Re-key**
  (random-password vaults: a new random password), **Show vault password**
  (random-password vaults, to open a download in another KeePass app),
  **⬇ Download .kdbx**, **Print household sheet**.
- **Versions** (⋯ menu): the last 20 saves since the vault's password last
  changed; restore any of them (it's saved as a new version). Changing a vault's
  password removes older versions, so an old version never opens with a password
  someone removed still knows.
- Two people can work in the same vault at once. If someone changed an item
  while you were editing it, you're told and can keep either version (the other
  stays in the item's history).

## Folders and items

- Folders live in each vault's tree in the sidebar: **+ Folder**, or a folder's
  ⋯ for *New subfolder*, *Rename*, *Move into…*, *Delete*. A breadcrumb above
  the list shows where you are. Deleting a folder with items moves it to that
  vault's **Trash**, where you can restore it. **Empty Trash** deletes for good.
  Folders show as groups in other KeePass apps, and imported files keep theirs.
- **+ New** adds an item in the folder you're in. Pick what it is — a **login**,
  **payment card** or **secure note**, or a ready-made form: **Wi-Fi, bank
  account, insurance policy, vehicle, passport / ID, driving licence, software
  licence, utility account, membership**. Forms fill in the usual fields;
  empty ones aren't saved. Add more fields with *More fields* (tick 🔒 for hidden
  ones), and tags.
- **2FA codes**: a login can hold a 2FA code, shown live with a countdown.
  **📷 Scan QR code** (camera, a screenshot — paste it with Ctrl+V, drop it or
  choose the file — or the key typed in), or paste the `otpauth://` link or setup
  key a site shows. The code is saved as `otp` (KeePassXC, KeePassDX, Strongbox)
  and as KeePass's own `TimeOtp-Secret-Base32`, so KeePass 2.47 and newer shows
  it too.
- **Password generator** in the editor: random (8–64 characters, choose the
  character sets) or words (with a separator and an optional number), with a
  strength meter.
- **Expiry dates**: any item can have an *Expires on* date (a card's MM/YY fills
  it in) and *Remind me* 1 to 90 days before. **🗓 Expiring** in the sidebar
  lists what has expired or expires in the next 90 days, and the list shows
  "expires in 12 days". It's KeePass's own expiry date, so KeePassXC shows it.
- In an item: 📋 copies (the clipboard is overwritten after 30 seconds by
  default — the app can't check whether you copied something else since),
  👁 reveals, ↗ opens the website. Card numbers are masked except the last 4.
  ⋯ has *Favourite*, *Move to…*, *Copy to…* (another folder or vault),
  *History* (earlier versions of the item), *Delete*.
- **Several at once**: tick items in the list to move, copy or delete them
  (Shift-click ticks a range; *Select all*).
- **Drag and drop** (computer): drag items onto a folder or vault in the sidebar
  to move them — hold **Ctrl** (Option on a Mac) to copy — or onto **Trash** to
  delete. Drag a folder onto another folder of the same vault to move it. On a
  phone use *Move to…*.
- The sidebar has **⭐ Favourites**, **🕘 Recently used** (kept for you across
  unlocks), **All items**, each vault's folders and Trash, **Tags** and type
  filters (logins, 2FA, notes, cards).

## Search

**Search** (`/` or `Ctrl+K`) looks through every open vault as you type: names,
usernames, websites ("amazon" finds amazon.in), tags, folders, custom field
names, card holders, the last 4 digits of cards, and notes if you turn that on
(Settings → Search). Words match at the start of a word; "quoted phrases" match
exactly. Filters: `tag:bank`, `folder:utilities`, `vault:household`,
`type:card`, `has:2fa`, `is:favourite`, `is:weak`, `is:reused`, `is:old`,
`is:breached`, `is:no2fa`. ↑/↓ and Enter open a result. Passwords and other
secrets are never searched. Vaults you haven't opened aren't searched.

## Password health

**🩺 Password health** lists the logins in your open vaults that need
attention, worked out inside Home Assistant:

- **Reused** — the same password on more than one item (across vaults);
- **Weak** — short or easy to guess;
- **Old** — not changed for over a year;
- **Breached** — found in known data breaches, if the **breach check** is on;
- **No 2FA** — the site offers codes from an authenticator app (from a small
  built-in list of well-known sites) but the login has no 2FA code.

Badges show in the item list too. Click an item to fix it.

**Breach check** (off by default): an admin allows it (*Admin → App settings*;
it needs internet), then each person turns it on (Settings or Password health).
It uses Have I Been Pwned's *Pwned Passwords*: the app sends only the first 5
characters of each password's SHA-1 hash and compares the answers itself — the
password and its full hash never leave Home Assistant. Results are kept until
you lock. New passwords are also checked as you type them.

## Household sheet

A printed page for the fridge or a drawer: the Wi-Fi, the alarm code, the
plumber's number. For the **Household** vault and your own **Personal** vault.

1. On an item: ⋯ → **Household sheet…** → *Include*, and tick the fields to
   print. For Wi-Fi, pick the security (WPA / WEP / open) to print a **Wi-Fi QR
   code** guests can scan — the network name is the Username (or the item's
   name), the password is the Password.
2. Vault ⋯ → **Print household sheet**.

Card numbers, CVVs, PINs and 2FA secrets are never printed. The choice is saved
inside the vault file, so it travels with downloads. Other members are alerted
when someone prints the sheet.

## 2FA codes from an authenticator app

**Settings → Import 2FA codes from an authenticator app**: in Google
Authenticator, ⋮ → *Transfer accounts* → *Export accounts*, and scan each QR
code it shows (camera or screenshot). You choose which accounts to add, and the
vault and folder; each becomes a login with its 2FA code. Counter-based (HOTP)
codes aren't supported.

QR codes are read **in your browser** (the camera needs HTTPS; if it's blocked
inside the Home Assistant app, use a screenshot or paste the key). Only the
resulting text goes to Home Assistant. Codes you already have are marked
("you already have this code in GitHub (Personal)") and left unticked.

## Guest Wi-Fi on your dashboard

Guests scan a QR code on your Home Assistant dashboard (or on the app's
**📶 Guest Wi-Fi** page, which works without unlocking) to join your guest
network. Off until someone publishes one.

1. In **Household**, make a **Wi-Fi** item (network name + password) — or on an
   existing login: ⋯ → *This is a Wi-Fi network*.
2. ⋯ → **Show as guest Wi-Fi on the dashboard**, pick the security, and whether
   to show the password as text too.
3. On a dashboard: *Add card* → **Picture entity** → `sensor.household_vault_guest_wifi`.

It follows the item: change the password and the dashboard updates; delete the
item (or *Stop showing*) to remove it. **This password leaves the encrypted
vault** on purpose — it's in the QR code, readable in the app's database and
in Home Assistant (including its history and backups). Use it for a guest
network, not your main one. To keep it out of Home Assistant's history, add to
`configuration.yaml`:

```yaml
recorder:
  exclude:
    entities:
      - sensor.household_vault_guest_wifi
```

## Emergency access

If something happens to you, people you trust can get the items you choose —
after a waiting period in which you can say no.

1. Mark items in your **Personal** vault: ⋯ → **🆘 Include in emergency access**.
2. **Settings → Emergency access**: add a contact (someone who's set up) and a
   waiting period (1–30 days).
3. If they need it, they tap **Ask for access** (their Settings). You're told
   at once (a banner when you unlock, and your phone if linked) and can
   **Deny** or **Approve now**. If you do nothing, they get your marked items,
   read-only, when the waiting period ends.

You can remove a contact at any time; if they already had access, the items get
a new password. Only marked items are ever included, and changes you make to
them are passed on while you're set up. While an admin has turned your access
off, nothing is released (you couldn't deny it) — so if the household wants
someone's emergency items after they've gone, don't turn their access off.

## Notifications

Your phone is set up once, in **Home Assistant**, not in this app:
**Settings → People → (the person)**:

1. **Allow person to login** links the person to their Home Assistant user.
2. **Track device** is where you pick their phone (the one with the Home
   Assistant Companion app).

Household Vault picks that phone up within 5 minutes and sends alerts to its
`notify.mobile_app_…` action — but only once the person is **enabled and set
up** here. Someone who isn't set up, or whose access is turned off, gets nothing
on their phone. When the phone is removed in Home Assistant, it stops getting
alerts here too.

**Admin → People → 🔔** shows each person's **Phones** from Home Assistant
(read-only; a phone whose Companion-app notify action can't be found is
flagged), with a hint when no Home Assistant person or phone is linked to the
login. **Check Home Assistant again** reads the people right away. Under
**Also**, an admin can add up to 5 *extra* notify services for this app only
(a speaker, a second service); *Send a test* sends to the phones and every extra
service. You get:

- **Security alerts**: several wrong master passwords, your master password
  changed, a copy of all your passwords downloaded, quick unlock set up; and
  when someone else downloads a shared vault, looks at its password, prints the
  household sheet or publishes the guest Wi-Fi.
- **Expiry reminders** for items with an expiry date (a few days before, and on
  the day).
- **Emergency access** requests and decisions.
- **Admin actions** that affect you (access turned off, a reset, a backup
  restored).

They never contain a password. Turn security alerts or expiry reminders off in
**Settings → Notifications**; emergency-access messages always go.

## Downloads, imports and copies

- **⬇ Download .kdbx** (vault ⋯): the vault's file, still encrypted. Personal
  opens with your master password; for a random-password vault use **Show
  vault password**.
- **Download all my passwords** (Settings): one `.kdbx` with everything you have
  open — Personal at the top, each other vault as a folder — locked with your
  master password (asked again). Optionally include deleted items (Trash). It
  opens in **KeePassXC** (computer), **KeePassDX** (Android) and **Strongbox**
  (iPhone), which can autofill from it. It's a copy: changes made in it don't
  come back. Settings shows when you last downloaded, and can **remind you**
  after 30, 60 or 90 days (and after a master-password change) with a banner
  when you unlock. Old copies keep opening with your old master password —
  delete them after a change.
- **Import passwords** (Settings): a KeePass file (`.kdbx`, with its password and
  key file if it has one), or a **CSV export** from Chrome, Edge, Firefox,
  Bitwarden, LastPass, 1Password, Dashlane or Proton Pass — as a new shared
  vault or into a folder of a vault you can edit. **Skip items I already have**
  (on by default) leaves out anything with the same name, username, website and
  password as an item in your open vaults, so importing twice adds nothing. A CSV
  export is not encrypted: delete it once it's imported. KDBX 3.1 and 4 files
  work; Twofish-encrypted files must be switched to AES or ChaCha20 in KeePassXC
  first. Everything another KeePass app wrote (attachments, icons, history,
  custom data) is kept.
- **Personal copies** (Admin → App settings, off by default): each person gets
  `/data/copies/<Home Assistant user name>.kdbx`, holding Personal and every
  vault they've remembered, locked with their master password. It's rewritten a
  few seconds after any change, or at their next unlock if they weren't unlocked
  (the master password is only known while they are). It's in Home Assistant's
  backups of the app and in the admin's *Download backup*. It's deleted when
  the person is disabled or reset, and all of them when the setting is turned
  off. Settings shows your file; Admin lists everyone's.
- **Emergency Kit** (Settings): a printable sheet with your name, a line to write
  your master password on, and how to open your passwords in KeePassXC if Home
  Assistant is down.

## Your settings

**Settings** (from the sidebar) holds your own choices, used on all your
devices: **Quick unlock** devices; **Locking** (*Lock after*, *When I switch to
another tab or app*, *Lock everywhere*); **Notifications** (security alerts,
expiry reminders); **Search** (also search notes); **Breach check**; **Master
password** (change it — you need the current one; your other devices are
locked, and you're reminded to print a new Emergency Kit and download a new
copy); **Emergency access**; **Download all my passwords**; **Import
passwords**; **Import 2FA codes**; **Emergency Kit**. The theme (**Vault**,
**Slate**, **Daylight** or **Auto**) is picked at the bottom of the sidebar.

## Admin

Admins are the Home Assistant users listed in the **admin_users** option. They
open **🛡 Admin** from the sidebar (or **Manage people** on the lock screen,
without unlocking).

- **People**: everyone with a Home Assistant login, with their status (*Not set
  up*, *Waiting for first unlock*, *Active*). **Enable and set up**, the
  **Access** switch (turning it off signs them out and takes them out of
  Household and shared vaults, which get new passwords; their Personal vault is
  kept; turning it back on returns them to Household), **Reset…** (only for a
  forgotten master password; type their name to confirm), **Unblock** after too
  many wrong passwords, and **🔔** for their phones and extra notify services.
- **App settings** (below).
- **Personal copies**: who has a file and when it was last written.
- **Backup and restore**: **Download backup** is one zip with the database, every
  vault file and every personal copy — all encrypted. **Restore…** replaces
  everything, locks everyone and lists what it undid (people removed and
  passwords changed after the backup was taken) so you can redo it; everyone
  sees the same list when they next unlock.
- The Argon2 settings tuned for this machine, how many sessions are unlocked and
  vaults open, and the app's version.

Admins can never open anyone's vaults through the app.

## App settings

**Admin → App settings**. Changes apply at once, without a restart.

| Setting | Default | What it does |
|---|---|---|
| Clear the clipboard after (seconds) | 30 | Overwrites a copied password after this long; 0 = never (0–600). |
| Shortest master password | 12 | Minimum length for master passwords and same-password vaults (8–64); a "good" strength score is also required. |
| Allow the breach check | off | Lets people turn on the Have I Been Pwned check (needs internet). |
| Longest unlocked session (hours) | 12 | Even an active session locks after this long (1–72). |
| Name the item in expiry reminders | on | Off: reminders don't name the item, and its name isn't kept readable outside the vault. |
| Keep one file per person in /data/copies | off | The personal copies described above. |

The app's **Configuration** tab has a single option:

| Option | Default | What it does |
|---|---|---|
| `admin_users` | empty | Home Assistant user names or user ids (not display names) of the admins. Restart the app after changing it (Information tab → **Restart**). |

## Who can see what, and where data goes

- **Vault contents** (passwords, notes, cards, 2FA secrets) are only inside the
  encrypted KeePass files, and decrypted only in the app's memory while
  someone who may open that vault is unlocked. Lists and search results never
  include passwords — the page asks for one field when you copy or reveal it.
- **Readable on purpose**, each only if you use it: **vault names** and who is in
  each vault; the **names and dates of items with expiry reminders** (an admin
  can turn the names off); the **guest Wi-Fi** you publish. An audit log records
  actions (who, what, when) but never contents.
- **Sent outside your home network**: only the breach check, if an admin allows
  it and you turn it on — the first 5 characters of password hashes go to
  `api.pwnedpasswords.com`. Nothing else leaves Home Assistant, and site icons
  are never fetched.
- **Sent to Home Assistant**: notifications (no secrets; names, vault names and
  network names), and the guest Wi-Fi sensor if published. The app reads the
  people (`person.*`) and their phones from Home Assistant.

## Backups

- Home Assistant's own backups include the app's data: the database, every
  vault file (encrypted) and any personal copies.
- **Admin → Download backup** makes the same thing as one zip.
- A backup restores files locked with the passwords they had then. Backups taken
  before a password change keep the old files, locked with the old password,
  until they rotate out.
- And keep your **own** KeePass copy (*Download all my passwords*) — see the
  notice at the top.

## Security, in short

- Vault files are always encrypted (AES-256, Argon2id key derivation) on disk,
  in Home Assistant backups and in downloads.
- The admins who run Home Assistant are trusted: someone with root on the Home
  Assistant machine could read the app's memory while vaults are open.
  Protect admin and SSH access, and install apps and frontend cards only
  from sources you trust — anything else running in the Home Assistant page
  could read what this page shows.
- Turn on Home Assistant multi-factor authentication; use HTTPS.
- Copied passwords can be read by clipboard-history tools; prefer 👁 on shared
  screens.
- Quick unlock trusts the device: anyone who can unlock that phone or computer
  with its fingerprint or PIN can unlock your vault on it. Remove devices you no
  longer use.
- Someone removed from a vault keeps whatever they saw or downloaded: change
  important passwords they knew.
- An independent security review is still to come.

## Limits

- Made for a household (a handful of people), not an organisation.
- 20 MB per vault file, 5,000 items per vault, 50 shared vaults, 20 people per
  vault, the last 20 versions of each vault, 200 search results.
- No browser or phone autofill from the app itself, and no access outside the
  Home Assistant panel — use *Download all my passwords* in a KeePass app for
  that.
- File attachments can't be added or viewed here (attachments in imported files
  are kept).
- On a Raspberry Pi 3 or slower, unlocking can take a few seconds (the Argon2
  settings have a safe minimum).

## Troubleshooting

- **"No admin yet" banner**: add the user name it shows to **admin_users** in the
  app's Configuration tab, save, and restart the app. Display names don't count; the
  **How the app sees you** page shows your exact user name and id.
- **"Ask an admin to give you access"**: an admin has to **Enable and set up**
  you in Admin → People.
- **You aren't listed in Admin → People**: open Vault once yourself, or link the
  Home Assistant user to a person (Settings → People) and tap **Check Home
  Assistant again**.
- **"Try again in N minutes"**: too many wrong master passwords; wait, or ask an
  admin to **Unblock** you.
- **Forgot your master password**: it can't be recovered; an admin can **Reset**
  you (your Personal vault is wiped).
- **No alerts on your phone**: link your phone in Home Assistant (Settings →
  People → you → Track device) and make sure you're set up here; Admin →
  People → 🔔 → *Send a test* shows what happens.
- **Camera or quick unlock doesn't work**: both need HTTPS; inside the Home
  Assistant phone app use a screenshot for QR codes and the browser for quick
  unlock.
- **A shared vault shows 🔒 after unlocking**: its password changed — ask its
  owner for the new one.
- **Import fails**: check the file's password and key file; Twofish-encrypted
  files must be switched to AES or ChaCha20 in KeePassXC first.
