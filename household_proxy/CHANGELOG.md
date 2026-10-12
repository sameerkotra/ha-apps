# Changelog

All notable changes to Household Proxy. See the [repository](https://github.com/sameerkotra/ha-apps).

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
