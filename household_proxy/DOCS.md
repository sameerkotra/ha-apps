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

## Monitoring

Any site can be **checked** on a schedule, whether or not anyone has the app open. Set it up in the site's form,
under **🩺 Check**:

- **Kind** — **Web page** (a `GET` of the start path or the **Path to check**, redirects not followed, the site's
  certificate rules and extra headers; up when it answers below 500, or only 2xx/3xx if you choose), **Port** (a
  connection to `host:port`) or **Ping** (three echoes; up if any is answered). If the app's container doesn't allow
  ping, the check says so: use **Port** instead.
- **Every** (60 seconds; 15 seconds to 24 hours), **Timeout** (5 seconds; 1–60) and **Down after** (3 failed checks
  in a row; 1–10), so one lost answer never counts as down. One good check is enough to be up again.
- **Home Assistant sensor** (on by default) — `binary_sensor.household_proxy_<short name>`, class
  *connectivity*: on while up. Attributes: `status` (up, down, restarting, waiting, needs_attention, paused,
  not_checked), `since`, `last_check`, `response_ms`, `restarts` (in the current incident) and `last_restart`. Use it
  in your own dashboards and automations. It is sent again every 5 minutes (Home Assistant forgets it when it
  restarts) and removed when the site or its check is.

### Monitor only

A site's **Use** can be **Monitor only**: it is checked (and can be restarted), never opened through the proxy and
never shown on the Sites page. Its address can be a URL, a host name or address alone (for Ping), or `host:port`
(for Port). No "trusted software" confirmation is needed: nothing of it runs on Home Assistant's address.

### The Monitor page

**Monitor** (next to **Sites**) lists every checked site you may see — the same people who may open a site see its
status. For each: its state and since when, the answer time, and the last 24 hours in half hours (green: up, red:
down, amber: some of each). **🔔 / 🔕** mutes a site's alerts for you alone. Admins also get **Check now**,
**🔌 Restart now** and **Try again** (for sites with auto-restart), **Pause…** (for 1 hour, 8 hours or until
resumed — for maintenance; nothing is checked or restarted while paused), **Resume** and **History**: the incidents of the last 90 days (start, end, cause, restarts, outcome) and the uptime
over 24 hours, 7 and 30 days. Tiles on the Sites page show a status dot for checked sites.

### Auto-restart

Under **🔌 Auto-restart** in a checked site's form, choose the **switch that powers the device** (a smart plug,
from Home Assistant's switches, with its current state) and confirm that it **doesn't power Home Assistant, your
router or your network switch** — turning one of those off would cut Home Assistant off from the device, and from
turning it back on. Then:

- **Off for** — how long the power stays off (5 seconds; 1–300).
- **Wait after restart** — no checks for this long while the device starts (120 seconds; 10 seconds to an hour).
- **Restarts before giving up** — 3 by default; 0 means never restart, only report (up to 10).
- **Only restart from … until …** (optional) — a time window in Home Assistant's time zone, e.g. 02:00–06:00.
  Outside it the site is reported down, and restarted when the window opens if it is still down.

How one incident goes: the site goes **Down** → a restart, if any are left and the safety rules allow it → **Waiting
after restart** → one check: up closes the incident; down means the next restart → when all are used, **Needs
attention**: checks go on, no more restarts until the site is up again or an admin presses **Try again** (the count
starts over).

The safety rules:

- **Never left off.** Each step is written down before it is taken. If the app (or Home Assistant) restarts between
  "off" and "on", the switch is turned on first, as soon as it can be; a failed "on" is retried every 30 seconds, and
  after 10 minutes the admins are told (and it keeps being retried).
- **Not when the network is the problem.** Before each restart the **Network reference address** (App settings,
  e.g. your router) must answer, and — when at least 3 sites are checked — more than half of them must not have gone
  down within the same 2 minutes. Otherwise nothing is restarted: *the network looks down, not the device*.
- **A switch Home Assistant can't reach** (unavailable or unknown) is never used.
- **One sequence per switch**: sites sharing a switch are restarted once, together, and it counts for each.
- **No restart loops**: an incident's first restart waits at least the **Gap between two incidents' restarts** (App
  settings, 10 minutes) after the site's last restart.
- Only `switch.turn_off` and `switch.turn_on`, only on the switch chosen for that site. Every restart is in the site's
  **History** and in Home Assistant's logbook.

When a wanted restart doesn't happen, the Monitor page says why ("not restarted: …"), and the site's people are told
once (**Couldn't restart**) — except for the time window and pausing, which are expected.

**Restart now** (admins, on the Monitor page; it asks first) restarts at once — it counts in the current incident if
there is one. The switch must be reachable; the network rules don't apply, since an admin decided.

### Alerts

Under **🔔 Alerts** in the site's form:

- **Tell** — the people to alert, from everyone who has opened the app: any people, not only those who may open the
  site. Every admin by default. Their phones come from Home Assistant (**Settings → People**, the Companion app
  devices tracked for each person).
- **Also send to** — an extra notify service for this site (a speaker, a group), e.g. `notify.living_room_speaker`.
- **About** — which events: **Down**, **Restarting** (each restart), **Back up** (with how long it was down and how
  many restarts), **Gave up**, **Couldn't restart**. All but **Restarting** by default.

One "down" per incident; nothing is repeated while a site stays down. Being alerted gives no access: the message
names the site and the event, and tapping it opens the Monitor page at that site, which shows the site only to
people allowed to see it.

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

**🩺 Monitoring**

- **Network reference address** — your router, or anything that should always answer (`host`, or `host:port`; a
  host alone is tried on ports 53, 80 and 443). Checked before each restart. Empty: only the "most sites down
  together" rule applies.
- **Gap between two incidents' restarts** — 10 minutes (0–1440).
- **Keep check history** — every check is kept for a week, then hourly totals for this long (30 days; 7–365).
  Incidents are kept 90 days.

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
- Alerts go only to the people chosen for each site (and its extra service), through Home Assistant.
- The database (`/data/proxy.db`) holds the sites, who may open them, the extra headers, the access logs, the check
  history and incidents, and the App settings. It is part of the app's Home Assistant backups.

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
