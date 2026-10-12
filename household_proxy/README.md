# Household Proxy — Home Assistant app

> ⚠️ **Unofficial app.** This is not an official Home Assistant app and isn't affiliated with or
> endorsed by Home Assistant or Nabu Casa. It was built with Claude, Anthropic's AI model, and is
> provided as-is: try it out first, and keep your own backups.

> **Trusted software only.** A site opened here runs on Home Assistant's own address, so add only
> software you run and keep updated.

Open the web pages of things on your home network — Komodo, a NAS, Pi-hole, Proxmox, a printer —
inside Home Assistant, from anywhere Home Assistant itself can be reached. Home Assistant's sign-in is
the only sign-in needed to reach them: no port is opened, no extra domain or tunnel is set up, and the
devices are never exposed to the internet. See the Documentation tab (DOCS.md) for the full guide.

- **Sites** — a tile for each site you may open; the site fills the panel, with a slim bar or none.
- **Who opens what** — admins only, everyone, or chosen people, per site.
- **Pages that expect their own address** — links, styles and redirects are rewritten so a site works
  under Home Assistant's address.
- **Signed in already** — an admin can add a header (a key or a sign-in) that the site gets and the
  browser never sees.
- **Admin** — sites, App settings, people, backup and restore.
