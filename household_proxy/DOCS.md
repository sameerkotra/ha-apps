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
**Copy link**.

Websockets (terminals, live logs) pass through both ways. The address a page asks for — its query too — reaches the
site as the browser sent it: Home Assistant escapes a few characters on the way (`[`, `]`, `{`, `}`, `|`, `^`, `` ` ``),
and the proxy puts them back (since 0.6.3; Komodo's terminal needs it).

**Cookies and browser storage** are kept per site. Every site shares Home Assistant's address in your browser, so the
app keeps each site's cookies under the site's own name (`hp_<short name>_<cookie>`, on the site's own path) and
passes only those to it, and the small script keeps each site's localStorage and sessionStorage keys apart in the same
way. A site can't read or wipe Home Assistant's sign-in, or another site's, by accident. (Upgrading to 0.6.4 renamed
them: sites that keep you signed in with a cookie asked you to sign in once.)

What the script can't reach, and so may not work: addresses a page writes into HTML as text (`innerHTML`,
`document.write`), requests made by a page's own background workers, a page sending the whole frame to a root address
(`location.href = "/login"`) in browsers without the Navigation API (Safari, Firefox — Chrome and Edge have it), and
browser storage used other ways than its methods (`localStorage.key = …`, IndexedDB, the Cookie Store API).

Most web pages expect to live at the top of their own address (`/`). Here they live under Home Assistant's
address, so the app rewrites what it can on the way through: links, forms, images, styles, redirects and
cookies. Big downloads and uploads stream through without waiting. Everything goes through Home Assistant —
and through Home Assistant Cloud when you're away — so pages, dashboards, terminals and logs are fine, while
video streams and large downloads are slow.

## Bookmarks

The **Bookmarks** tab holds the household's links — school, bins, recipes, anything on the web. Everyone sees
the same list. Select **＋ Add bookmark**, type the address (`example.com` is fine) and, if you like, a name and an
emoji icon.

Click one to open it: on a computer it opens in a new tab; on a phone it opens in Chrome (if Chrome isn't
installed on an Android phone, the normal browser). Untick **Open in Chrome** at the bottom of the tab (phones only)
to use your phone's normal browser instead — that's remembered on that phone.

Anyone can add a bookmark; the person who added it, or an admin, can change (✎) or delete it, and admins can
change the order (▲▼). Bookmarks are plain links: they don't go through the proxy, so a bookmark to a site on
your network only works where that site can be reached. Don't put passwords in an address — everyone can see it.

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
- **This is software I run and keep updated** — required. See **Trusted software only** below.
- **Single-page app mode** — for apps with their own router that show "not found" under the proxy (see above).
- **Show the bar** — on by default.

### Trusted software only

**A flaw in any site you list exposes Home Assistant for whoever opens it.** A site opened here runs on Home
Assistant's own address. A page there — or a script injected into it through a bug in the site — can read what Home
Assistant's pages keep in the browser, including the sign-in kept by "Keep me logged in", and act in Home Assistant as
the person viewing it, with everything that person may do. The app makes accidents unlikely — each site's cookies and
browser storage are kept apart, and requests to the app's own admin pages from a proxied page are refused — but none
of that stops a page written to get around it: the browser treats every site here as Home Assistant itself.

So: add only software you run and keep updated (Komodo, your NAS), not a router, printer or camera page with old
firmware; keep **Who can open it** on **Admins only** unless others need the site; and for anything you don't fully
trust, use a tunnel and a Webpage dashboard card instead (see Troubleshooting).

When you save a site, the form also warns (and saves anyway) when its address is on Home Assistant's own internal
network (Home Assistant, the Supervisor, your other apps) or is a link-local address.

**Advanced:**

- **Send Home Assistant's host name** — normally the site gets its own address as `Host`.
- **Remove the site's Content-Security-Policy** — only if the site's own policy blocks the proxy.
- **Pass the viewer's name** — adds `Remote-User` and `X-Forwarded-User` for sites that sign people in from a
  header. Only safe when the site accepts that header from this app alone. Such headers sent by a browser are always
  removed, whether this is on or off.
- **Keep an access log** — time, person, method, path and status for 7 days (App settings); never contents. A
  websocket has a line when it opens and one when it closes (with how long it was open and how much it carried).
- **Timeout** — this site's own request timeout (0 = the App setting).
- **Extra headers** — added to every request to the site, for example `Authorization: Basic …` or an API key, so
  the site opens already signed in. Values are shown as "saved", never sent back to a browser and never logged.
  **Everyone allowed on the site acts as that account.** When you change a site's address, enter the values again:
  saved values never follow a site to a new address.

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
hashes in the page (they no longer match). A rule can never add a header line. A rule applies to answers of up to
5 MB that say their length (HTML and CSS: also without), never to live updates (`text/event-stream`) or partial
downloads. An expression that repeats something that itself repeats without limit (`(a+)+`, `(x*y)*`) is refused:
on some pages it could take minutes.

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
down, amber: some of each). **🔔 / 🔕** mutes a site's alerts for you alone. Admins also get **Check now** (while a site is restarting or starting up, it shows the answer without counting
it), **🔌 Restart now** and **Try again** (for sites with auto-restart), **Pause…** (for 1 hour, 8 hours or until
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
after restart** → checks again: one good check closes the incident; **Down after** failed checks in a row (3 by
default) mean the next restart → when all are used, **Needs attention**: checks go on, no more restarts until the site
is up again or an admin presses **Try again** (the count starts over).

The safety rules:

- **Never left off.** Each step is written down before it is taken. If the app (or Home Assistant) restarts between
  "off" and "on", the switch is turned on first, as soon as it can be; a failed "on" is retried every 30 seconds, and
  after 10 minutes the admins are told (and it keeps being retried).
- **Not when the network is the problem.** Before each restart the **Network reference address** (App settings,
  e.g. your router) must answer, and — when at least 3 sites are checked — more than half of them must not have gone
  down within the same 2 minutes. Otherwise nothing is restarted: *the network looks down, not the device*.
- **A switch Home Assistant can't reach** (unavailable or unknown) is never used.
- **One sequence per switch**: sites sharing a switch are restarted once, together, and it counts for each site that
  is down. The others on the switch lose power too, so they show **Restarting**, then **Waiting after restart**, and
  aren't checked meanwhile (a site that gave up keeps **Needs attention**). Nothing on the switch is restarted while any
  site on it is paused or outside its restart window.
- **No restart loops**: after a restart, a site needs its **Down after** failed checks in a row again before the next
  one; a new incident's first restart waits at least the **Gap between two incidents' restarts** (App settings, 10
  minutes) after the site's last restart, and twice as long for each further restart within 24 hours (20, 40, 80
  minutes…, at most 12 hours); and a site is restarted automatically at most **Most automatic restarts per day** times
  in 24 hours (App settings, 6). Then it is reported (**Couldn't restart**), not restarted.
- Only `switch.turn_off` and `switch.turn_on`, only on the switch chosen for that site. Every restart is in the site's
  **History** and in Home Assistant's logbook.

When a wanted restart doesn't happen, the Monitor page says why ("not restarted: …"), and the site's people are told
once (**Couldn't restart**) — except for the time window and pausing, which are expected.

**Restart now** (admins, on the Monitor page; it asks first) restarts at once — it counts in the current incident if
there is one. The switch must be reachable and no site on it paused; the network rules, the restart windows and the
daily most don't apply, since an admin decided. **Try again** also starts the day's count over.

When you save a site with auto-restart while no **Network reference address** is set and fewer than 3 sites are
checked, the form warns you: the app then can't tell a network outage from a stuck device.

### Alerts

Under **🔔 Alerts** in the site's form:

- **Tell** — the people to alert, from everyone who has opened the app: any people, not only those who may open the
  site. Every admin by default. Their phones come from Home Assistant (**Settings → People**, the Companion app
  devices tracked for each person).
- **Also send to** — an extra notify service for this site (a speaker, a group), e.g. `notify.living_room_speaker`.
- **🔔 Send a test alert** (a saved site) — sends a test now to everyone above, as saved, and shows what Home
  Assistant said for each phone or service. Nobody gets it? Open **Admin → People → 🔔** for the person.
- **About** — which events: **Down**, **Restarting** (each restart), **Back up** (with how long it was down and how
  many restarts), **Gave up**, **Couldn't restart**. All but **Restarting** by default.

One "down" per incident; nothing is repeated while a site stays down. Being alerted gives no access: the message
names the site and the event, and tapping it opens the Monitor page at that site, which shows the site only to
people allowed to see it.

## Admin → People

Everyone who has opened the app. Someone turned off opens no site, whatever each site allows — and an admin turned
off by another admin has no admin pages either. You can't turn yourself off.

**🔔 Notifications** (as in the other household apps): the person's phones from Home Assistant (**Settings → People →
the person → Track device**, with the Companion app; "No person linked" means **Allow person to login** isn't set for
them), any extra notify services you add (a speaker, a second service — they get the site alerts too), and **Send a
test**. **Check Home Assistant again** re-reads the people after you change them there.

## Settings

### App settings (Admin → App settings)

**🌐 Proxy**

- **Request timeout** — how long to wait for a site to start answering (60 seconds; 5–600). Once a page,
  download or live stream has started, it isn't cut off.
- **Largest upload passed on** — bigger uploads are refused before they reach the site (100 MB; 1–4096).
- **Collect uploads sent without a size** — a browser sometimes sends an upload without saying its size first, and
  some sites refuse that: such uploads up to this size are collected first and passed on with their size; bigger ones
  stream on (8 MB; 0–64; 0: always stream).

One person can have at most 48 requests (downloads and live streams included) and 16 websockets open on one site at a
time; more get "Too many at once" until some finish.

**📜 Logs**

- **Keep access logs** — for the sites that keep one (7 days; 1–90).

**🩺 Monitoring**

- **Network reference address** — your router, or anything that should always answer (`host`, or `host:port`; a
  host alone is tried on ports 53, 80 and 443). Checked before each restart. Empty: only the "most sites down
  together" rule applies.
- **Gap between two incidents' restarts** — 10 minutes (0–1440); twice as long for each further restart within 24
  hours (at least 5 minutes from the second, at most 12 hours).
- **Most automatic restarts per day** — per site, whatever its incidents (6; 1–100). Restart now and Try again still
  work.
- **Keep check history** — every check is kept for a week, then hourly totals for this long (30 days; 7–365).
  Incidents are kept 90 days.

Changes apply at once.

### The app option (Configuration tab)

`admin_users` — who is an admin, by Home Assistant user name (login name) or user id. Display names are not
accepted. Restart the app after changing it.

## Who can see what, and where your data goes

- Only the Supervisor's ingress proxy can reach the app; Home Assistant has already checked who you are.
- A site is opened only by the people allowed on it, checked on every request (and every 30 seconds, and at once on a
  change, for an open websocket). Someone not allowed on a site gets "No such site", as for one that doesn't exist.
- The app connects only to the sites an admin added; a request can never name another address.
- Home Assistant's own sign-in details (its identity headers and its `ingress_session` cookie) are never passed
  to a site, nor are headers a browser makes up to look like a sign-in (`Remote-User`, also written `Remote_User`).
  A site's cookies are kept under its own name and path, and only they are passed to it, so they never reach Home
  Assistant or another site — and no other cookie reaches the site.
- A page opened through the proxy can't use the app's admin pages or change anything through the app (it is refused
  when the browser shows where the request came from) — but see **Trusted software only**: it is a safeguard, not a
  wall.
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
