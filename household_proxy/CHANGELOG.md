# Changelog

All notable changes to Household Proxy. See the [repository](https://github.com/sameerkotra/ha-apps).

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
