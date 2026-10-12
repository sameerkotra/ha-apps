# Household Proxy

Household Proxy opens the web pages of things on your home network — Komodo, a NAS, the router, a printer,
Pi-hole, Proxmox — inside Home Assistant, from anywhere Home Assistant itself can be reached (Home Assistant
Cloud, your own domain, a VPN). Home Assistant's sign-in is the only sign-in needed to reach them. No port is
opened, no extra domain or tunnel is set up, and the devices are never exposed to the internet: your browser
only ever talks to Home Assistant, and the app fetches each page from the device for you.

## Getting started

1. Install the app, start it and open it from the sidebar (**Proxy**).
2. Add your Home Assistant user name to `admin_users` on the app's Configuration tab, save and restart the
   app — until then every page shows "No admin yet" with the name to add.
3. Open **🛡️ Admin → Sites** and add a site: a name, an icon and its address on your network
   (`http://192.168.1.10:9120`). Choose who may open it.
4. Everyone else opens **Proxy** from the sidebar and sees a tile for each site they may open.

**How the app sees you** (your name, top right) shows who the app thinks you are and why you are (or
aren't) an admin.

## Sites

A tile for each site you may open. Select one to open it: the site fills the panel, with a slim bar on top —
**← Sites**, its name, **↻** (reload), **🔗 Copy link** and **↗** (the same site in a new tab). A site can
hide the bar (good for apps with their own navigation, like Komodo); a small **←** stays in the corner.

**Copy link** gives a link to the site through Home Assistant (`/<the app's page>/site/<short name>`). Use it
for a dashboard button (a "navigate" action), a notification or a bookmark: it opens Home Assistant, then the
site.

### What works, and what doesn't

The app also puts a small script first into every page of a site. While the page runs, it sends the requests,
websockets, live updates and new links the page builds (`/api/…`, `wss://…`) through the proxy as well.

**Single-page app mode** (per site): apps with their own router — React, Vue, Komodo — read their own address to
decide what to show, and under the proxy they see a long Home Assistant address and show "not found". In this mode
the frame's address is changed to the site's own path before the app starts, so its router works. Then **↻** in the
bar starts the site again from its start page, and the frame's own address isn't a link anyone can open — use
**Copy link**. Pages that read their own cookies from a script may not work in this mode.

Websockets (terminals, live logs) pass through both ways.

Most web pages expect to live at the top of their own address (`/`). Here they live under Home Assistant's
address, so the app rewrites what it can on the way through: links, forms, images, styles, redirects and
cookies. Big downloads and uploads stream through without waiting. Everything goes through Home Assistant —
and through Home Assistant Cloud when you're away — so pages, dashboards, terminals and logs are fine, while
video streams and large downloads are slow.

## Admin → Sites

Add, edit, delete and reorder the sites. For each site:

- **Name**, **Icon** (an emoji) and **Short name** — the short name is part of its address (`…/s/komodo/`);
  lower-case letters, digits and `-`. Made from the name when left empty.
- **Address** — the site's address on your network: `http://` or `https://`, host and port, no path.
  Private and link-local addresses, single-label names and `.local`, `.lan`, `.home.arpa` names that point
  at them are allowed. An internet address needs **Allow an internet address** (Advanced). The address is
  checked again each time the app connects, not only when you save.
- **Start path** — the page the tile opens (`/dashboard`).
- **Who can open it** — **Admins only** (the default), **Everyone**, or **Chosen people** (everyone who has
  opened this app is listed). Admins open every site.
- **This is software I run and keep updated** — required. A site opened here runs on Home Assistant's own
  address: a page there can read what Home Assistant's pages keep in the browser — including the sign-in kept
  by "Keep me logged in" — and act in Home Assistant as the person viewing it. Add only software you run and
  keep updated (Komodo, your NAS), not a router, printer or camera page with old firmware.
- **Single-page app mode** — for apps with their own router that show "not found" under the proxy (see above).
- **Show the bar** — on by default.

**Advanced:**

- **Send Home Assistant's host name** — normally the site gets its own address as `Host`.
- **Remove the site's Content-Security-Policy** — only if the site's own policy blocks the proxy.
- **Pass the viewer's name** — adds `Remote-User` and `X-Forwarded-User` for sites that sign people in from a
  header. Only safe when the site accepts that header from this app alone.
- **Keep an access log** — time, person, method, path and status for 7 days (App settings); never contents.
- **Timeout** — this site's own request timeout (0 = the App setting).
- **Extra headers** — added to every request to the site, for example `Authorization: Basic …` or an API key, so
  the site opens already signed in. Values are shown as "saved", never sent back to a browser and never logged.
  **Everyone allowed on the site acts as that account.**

### Test

**🔍 Test** on the form fetches the start page through the proxy, from Home Assistant, and says in plain words what
it found: whether the site answers (and why not — refused, timed out, name not found, certificate), its status and
time, redirects, frame-blocking headers (the proxy removes them), how many addresses in the page are rewritten, and
whether its main scripts use a client-side router or a fixed base path. The verdict is **Should work**, **Try
single-page app mode**, **Needs a rule** (with the suggested rule — **Add it**), or **Probably won't work through
Home Assistant — use a tunnel** (for example when the site sends you to another address to sign in). The Test can
run before you save; extra headers left blank use the saved values.

### Self-signed certificates

A site on `https://` with a self-signed certificate (most NAS and Proxmox installs) is refused at first: the Test
shows its certificate — name, expiry and SHA-256 fingerprint. After saving the site, **Trust this certificate**
accepts that exact certificate for this site from then on, whatever its name and dates; any other certificate is
still refused. Check the fingerprint against the one the device shows if you can. Changing the site's address forgets
the trusted certificate.

### The site's own icon

**Use the site's own icon** fetches the site's icon when you save and shows it on the tile instead of the emoji.
Only small PNG, ICO, GIF, JPEG or WebP images are kept (SVG never: it can contain scripts).

### Rules (advanced)

For the site that hard-codes something the proxy misses: a regular expression and its replacement, applied to the
**body** or to the **header values** of answers of the listed content types (for example
`application/javascript`; empty means every type). `$prefix` in the replacement is the site's prefix here
(`/api/hassio_ingress/…/s/<short name>`); `\1` refers to a group. Scripts changed by a rule lose their integrity
hashes in the page (they no longer match). A rule can never add a header line.

## Admin → People

Everyone who has opened the app. Someone turned off opens no site, whatever each site allows. You can't turn
yourself off.

## Settings

### App settings (Admin → App settings)

**🌐 Proxy**

- **Request timeout** — how long to wait for a site to start answering (60 seconds; 5–600). Once a page,
  download or live stream has started, it isn't cut off.
- **Largest upload passed on** — bigger uploads are refused before they reach the site (100 MB; 1–4096).

**📜 Logs**

- **Keep access logs** — for the sites that keep one (7 days; 1–90).

Changes apply at once.

### The app option (Configuration tab)

`admin_users` — who is an admin, by Home Assistant user name (login name) or user id. Display names are not
accepted. Restart the app after changing it.

## Who can see what, and where your data goes

- Only the Supervisor's ingress proxy can reach the app; Home Assistant has already checked who you are.
- A site is opened only by the people allowed on it, checked on every request.
- The app connects only to the sites an admin added; a request can never name another address.
- Home Assistant's own sign-in details (its identity headers and its `ingress_session` cookie) are never passed
  to a site. A site's cookies are kept to that site's own path, so they never reach Home Assistant or another
  site.
- A form or script on another web site can't change anything through the app or through a site: such requests
  are refused.
- Nothing leaves your home network unless a site's address is an internet address you allowed.
- The database (`/data/proxy.db`) holds the sites, who may open them, the extra headers, the access logs and the
  App settings. It is part of the app's Home Assistant backups.

## Backups

**Admin → Storage → Download a backup** saves the whole database. The values of the extra headers are left out
of it (their names are kept); a restore keeps the values this install has for the sites that still exist.
**Restore a backup** replaces every site and setting with the backup's.

## Troubleshooting

- **"No admin yet"** — add the name it shows to `admin_users` and restart the app.
- **A blank page or "not found" inside the site** — try **Single-page app mode**. Some apps can't work under
  another address at all; the honest answer for those is a tunnel (Cloudflare Tunnel, Tailscale) and a Webpage
  dashboard card.
- **"The site refused the connection"** — check the address and port, and that the device is on.
- **"The name … couldn't be found"** — Home Assistant's DNS doesn't know a `.local` name: use the IP address.
- **"…is an internet address"** — the name points outside your network; allow it per site only if you mean to.
- **The site's certificate wasn't accepted** — the site uses a self-signed certificate: run the Test, check the
  fingerprint and choose **Trust this certificate**.
