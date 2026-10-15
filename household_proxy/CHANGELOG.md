# Changelog

All notable changes to Household Proxy. See the [repository](https://github.com/sameerkotra/ha-apps).

## 0.6.5

### Added

- **Admin → People** is now the same page as the other household apps: each person's **🔔 Notifications** shows the
  phones Home Assistant links to them (or why there are none), lets you add an extra notify service, and has
  **Send a test**.
- **🔔 Send a test alert** under a site's Alerts: a test to everyone that site's alerts go to, as saved, with what
  Home Assistant said for each.

### Fixed

- Site alerts (and the "a switch may be off" note to admins) now also go to each person's extra notify services, not
  only to the phones Home Assistant links to them — so someone with no tracked phone can still be alerted.

## 0.6.4

### Security

- Each site's cookies are now its own: in your browser they are named after the site (`hp_<short name>_…`) and only
  those reach it — never Home Assistant's cookies or another site's. **After the upgrade, sites that keep you signed in
  with a cookie may ask you to sign in once.**
- Each site's browser storage (localStorage, sessionStorage) is kept apart too, so a site can't read or clear Home
  Assistant's (or another site's) by accident. Sites that keep a sign-in or settings there may ask for them once.
- A page opened through the proxy can no longer use the app's admin pages or change anything through the app (a
  speed bump, not a wall — see "Trusted software only" in the documentation, which now says plainly what a site you
  add can do).
- A browser can no longer slip a sign-in header past the proxy by writing it with underscores (`Remote_User`).
- An open websocket (a terminal, live logs) is closed as soon as its person is turned off or taken off the site.
- A site you may not open now answers "No such site", the same as one that doesn't exist (no guessing short names).
- Rewrite rules that could take minutes on some pages (a repeat inside a repeat, like `(a+)+`) are refused when saved.
- The site form warns about addresses on Home Assistant's own internal network and link-local addresses.

### Fixed

- Sites sharing a smart plug: a restart now marks every site on the plug (none is checked while it starts), and none
  is restarted while one of them is paused or outside its restart window. The switch is still never left off.
- After a restart, a site needs its usual number of failed checks in a row (3 by default) before the next restart,
  not one.
- A site that keeps going down and coming back is restarted at most 6 times a day (new App setting **Most automatic
  restarts per day**), and each further restart within 24 hours waits twice as long as the one before.
- **Check now** while a site is restarting or starting up shows the answer without counting it, and a check that was
  running when a restart began no longer counts.
- After **Restart now** on a healthy site, it shows **Up** again after its first good check (it stayed on "Waiting").
- The Monitor page's refresh no longer closes a **Pause…** menu you have open.
- Paused sites show as paused on the Sites page until the pause ends, not until the next change.
- Uploads a browser sends without saying their size reach the site with their size (up to 8 MB, new App setting
  **Collect uploads sent without a size**); bigger ones stream on as before.
- Live updates (server-sent events) and partial downloads are never held back or changed by rewriting; a page's own
  `<meta>` Content-Security-Policy is cleaned like the header (and removed with **Remove the site's
  Content-Security-Policy**); gzip answers in several parts are read to the end; websockets keep their connection
  alive, try each address of a site, and pass on addresses with non-ASCII characters.
- A site's policy with only `default-src` gets its own `script-src` for the proxy's script, instead of loosening
  `default-src` for everything.
- Pages that make their own kind of WebSocket (a subclass) keep working.

### Changed

- The image is built on one fixed Alpine release, so a rebuild can't quietly move to the next one.
- Websockets now have access-log lines (opened, and closed with how long and how much), for sites that keep a log.
- One person can have at most 48 requests and 16 websockets open on a site at once; at most 3 pages are rewritten at
  the same time.
- Faster: the Monitor page and the checks read less from the database, a slow check no longer holds up the others,
  name lookups and certificates are reused, the access log is written in batches, and nothing is written for a
  person on every page load.
- Shared code: answers from the app's own API are never cached by the browser, its scripts and styles are kept until
  the next version (faster loading), request bodies are limited even when sent in pieces, and database writes need
  one disk sync less (a power cut can lose the last moment's changes, never the file).

## 0.6.3

- Fix: Komodo's terminal said "connection closed" straight away. Home Assistant's ingress escapes the brackets in
  addresses like `/ws/terminal?target[type]=…` on the way to the app, and Komodo refuses the escaped form. The proxy
  now passes the query on as the browser sent it (for websockets and ordinary requests alike).

## 0.6.2

- Shared code: a page can't set inline event handlers, safer App settings saves (no lost change when two admins save
  at once) and clearer backup size messages.

## 0.6.1

- Fix: the app didn't start inside Home Assistant ("module 'app.config' has no attribute 'SUPERVISOR_CORE_API'").
  A new test starts the app with a Supervisor token against a fake Home Assistant, so this can't slip through again.

## 0.6.0

- Listed in the app repository, with its documentation; ready to try. Not yet tried on real hardware with Komodo and
  a smart plug: please report what works and what doesn't.
- Fixes from an independent review: a browser can no longer send its own `Remote-User` (or other sign-in headers)
  to a site; `TRACE` isn't passed on (it would echo the extra headers); a site's saved header values never follow it
  to a new address (enter them again), nor move to another address in a restore; a switch turned off for a restart
  is turned back on even if its site is changed or deleted meanwhile; **Restart now** on a healthy site and **Try
  again** behave as expected; a manual and an automatic restart can't run on one switch at once; an admin turned off
  by another admin loses the admin pages.

## 0.5.0

- **Auto-restart**: a site that stops answering can be power-cycled with the Home Assistant switch (a smart plug) that
  powers it — off, a few seconds, on — up to a set number of times, then the app gives up and says so (**Needs
  attention**; **Try again** starts the count over). Off for, wait after restart, restarts before giving up and an
  optional time window are set per site.
- Safety rules: a switch this app turned off is never left off (turned on first at start-up, retried every 30 seconds,
  admins told); no restart when the network looks down (a reference address of your choice doesn't answer, or most
  checked sites went down together) or when Home Assistant can't reach the switch; sites sharing a switch are
  restarted once; a gap between incidents' restarts; nothing while paused.
- **Restart now** (admins, asks first) on the Monitor page; every restart is in the site's history and Home
  Assistant's logbook.

## 0.4.0

- **Monitoring**: any site can be checked on a schedule — a web page, a port or a ping — and goes down only after
  several failed checks in a row (3 by default), up again after one good check.
- **Monitor only** sites: watched, never opened through the proxy (a printer, a switch, anything with a port or a
  ping).
- **Monitor** page: each checked site you may see, its state and since when, its answer time and the last 24 hours;
  admins also get **Check now**, **Pause** (1 hour, 8 hours or until resumed) and **History** (incidents and
  uptime over 24 hours, 7 and 30 days). Tiles show a status dot.
- **Alerts** to the people chosen per site (any people; every admin by default), on their phones from Home
  Assistant, plus an optional extra notify service; per event (down, back up, …); each person can mute a site for
  themselves. An alert opens the Monitor page at that site.
- A **binary sensor** per checked site in Home Assistant (`binary_sensor.household_proxy_<short name>`).

## 0.3.0

- **Test** on the site form: whether the site answers (and why not: refused, timed out, name not found,
  certificate), its status and time, redirects, frame-blocking headers, what is rewritten, and whether its scripts use
  a router or a fixed base path — with a verdict and, when it helps, a suggested rule or "try single-page app mode".
- **Trust this certificate** for a site with a self-signed certificate: only that exact certificate is accepted.
- **Use the site's own icon** on its tile (small PNG, ICO, GIF, JPEG or WebP; never SVG).
- **Rules** (advanced): a regular expression and its replacement for the body or headers of chosen content types,
  for the site that hard-codes something the proxy misses.
- Compressed answers are read in bounded pieces, so a small compressed page can't fill the memory.

## 0.2.0

- Pages that build addresses while they run work too: a small script, put first into every page, sends their requests,
  websockets, live updates, workers and new links through the proxy.
- **Single-page app mode** (per site): apps with their own router (React, Vue — Komodo is one) see their own paths.
- Websockets pass through both ways (terminals, live logs), with the same check of who may open the site.

## 0.1.0

- First version: sites opened inside Home Assistant through its sign-in (ingress), at `…/s/<short name>/`.
- Who opens each site: admins only, everyone, or chosen people; admins open every site; people can be
  turned off in **Admin → People**.
- Pages written for their own address work under Home Assistant's: links, forms, images, styles,
  redirects and cookies are rewritten on the way through; big downloads and uploads stream.
- Only the sites an admin lists can be reached; internet addresses only when allowed per site; the
  address is checked when connecting, not only when saved.
- Home Assistant's own sign-in details and cookies never reach a site; a site's extra headers never
  reach the browser and are left out of backups.
- Requests from other web sites are refused, for the sites too.
