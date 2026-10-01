# Household Vault

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

> **Experimental.** Household Vault is a password manager that hasn't had an
> independent security review yet. Keep your own KeePass copy of your passwords
> (**Settings → Download all my passwords**, opens in KeePassXC, KeePassDX or
> Strongbox) and don't make this app your only copy.

A password manager for the whole household, inside Home Assistant. Every vault
is a standard KeePass (KDBX 4) file, encrypted at rest with its own password and
opened in memory only while someone who may use it is unlocked. Each person gets
a Personal vault, everyone shares a Household vault, and anyone can make shared
vaults with a password they choose or a random one. People sign in with their
Home Assistant login; nobody gets in until an admin sets them up, and admins
can't open anyone's vaults.

- Folders, instant search, favourites, tags and recently used
- Logins with 2FA codes (scan the QR code), cards, secure notes and ready-made forms
- Password generator, password health and an opt-in breach check
- Download everything as one KeePass file; import KeePass files and CSV exports
- Emergency access, expiry reminders, phone alerts, quick unlock with a passkey
- A printable household sheet and an optional guest Wi-Fi QR code for your dashboard

See the **Documentation** tab (`DOCS.md`) for setup and the full guide.
