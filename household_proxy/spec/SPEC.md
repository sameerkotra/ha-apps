# Household Proxy — spec

Status: **built; 0.6.5 current** (spec 2026-10-10; monitoring and auto-restart added 2026-10-11; §11 steps 1–5 built
and tested 2026-10-11, 0.6.0 listed in the repository; 0.6.3 fixed Komodo's terminal; 0.6.4 (2026-10-13) the fixes of
the 13 Oct review — §14). Step 6's real-hardware run — two or three sites besides Komodo, a smart plug — is still to
do. This spec describes 0.6.5 as built (0.6.5: the shared Admin → People notify editor and test alerts, §13.4). Decided: the
name *Household Proxy*; everyone sees the panel, each person only the sites they may open; Isolated sites come in a
later version (§14).

A new app, `household_proxy`, that opens the web pages of things on the home network — Komodo, a NAS, the router,
a printer, Pi-hole, Proxmox, a camera's settings page — **inside Home Assistant**, from anywhere Home Assistant
itself can be reached (Home Assistant Cloud, your own domain, a VPN). Home Assistant's sign-in is the only sign-in
needed to reach them; no port is opened, no extra domain or tunnel is set up, and the devices themselves are never
exposed to the internet.

## 1. Purpose, and what it is not

- **Any number of sites.** A *site* is a name, an icon and a target address on the home network
  (`http://192.168.0.20:9120`, `https://nas.lan:5001`). An admin adds them; each person sees the sites they may open.
  Komodo was the reason for it, but nothing in the app is specific to Komodo.
- **Watched, and restarted when stuck** (§13). Any site can be checked on a schedule (web page, port or ping);
  when it stops answering, the app can power-cycle it with a Home Assistant switch (a smart plug): off, a few
  seconds, on — as many times as an admin allows, then it gives up and tells someone. A site can also be
  **monitor only**: watched and restarted, but not opened through the proxy.
- **Through Home Assistant's ingress.** The browser only ever talks to Home Assistant; Home Assistant's Supervisor
  passes the request to this app; the app fetches the page from the site and sends it back. Home Assistant has
  already checked who the person is.
- **Not an open proxy.** Only the targets an admin lists can be reached, and only by the people allowed on each.
  A request can never name its own host.
- **Not a VPN or a tunnel**, and not for heavy traffic: every byte goes through Home Assistant (and through Home
  Assistant Cloud when you're away). Pages, dashboards, terminals and logs are fine; video streams and large
  downloads are slow and use the Cloud connection.
- **Not guaranteed for every web page.** Pages built to live at the top of their own address (`/`) are moved
  under a sub-path here (§3). The app rewrites what it can, the **Test** button says how likely a site is to work
  (§5.3), and for the few that can't work this way the honest answer is a tunnel (§12).

## 2. How a request flows

```
browser ──https──▶ Home Assistant  /api/hassio_ingress/<token>/s/komodo/stacks
                     │  (HA login checked; Supervisor ingress proxy, websockets included)
                     ▼
               household_proxy :8099   /s/komodo/stacks
                     │  site "komodo" allowed for this person? → strip the prefix, clean the headers
                     ▼
               http://192.168.0.20:9120/stacks   (the site)
                     │
               response ◀── rewritten (§3), headers cleaned (§4), streamed back
```

Paths inside the app:

| Path | What |
|---|---|
| `/` | the app's one page: the launcher (tiles for the sites this person may open), and as views of it (`#/site/<site>`, `#/monitor`, `#/admin`) the viewer — a slim bar (optional, §5.1) and the site in a frame —, the Monitor page (§13.6) and Admin |
| `/s/<site>/<path>` | the site itself, proxied (every method, websockets too) |
| `/_hp/shim.js`, `/_hp/…` | the app's own scripts injected into proxied pages (§3.3) |
| `/api/…` | the app's own API (§9) |
| `/site/<site>[/<path>]` | deep link from Home Assistant (shared `deeplinks`), redirects to the viewer |
| `/monitor/<site>` | deep link from a monitoring notification (§13.4), opens the Monitor page at that site |

`P` below means a site's public prefix: the `X-Ingress-Path` the Supervisor sends + `/s/<site>`.

## 3. Making a root-path site work under a sub-path

The core problem: a site expects to be at `/`, so its page asks for `/assets/app.js`, `/api/read`,
`wss://host/ws`. Inside Home Assistant those would go to Home Assistant itself. Four layers fix it, each per site.

### 3.1 Requests (browser → site)
- Strip `P`; normalise the rest (no `..`, no `//host`, no encoded slashes that change the path). The target's
  scheme, host and port always come from the site, never from the request.
- `Host` = the target's host (option: keep the original), `Origin` and `Referer` rewritten from Home Assistant's
  address to the target's, so sites that check them (CSRF guards) are satisfied.
- **Never forwarded**: `X-Remote-User-*`, `X-Ingress-Path`, `X-Hass-*`, `X-Forwarded-*` from the Supervisor,
  header sign-in names (`Remote-User`, `X-Forwarded-User`, …) a browser sent, and Home Assistant's cookies. A header
  name is compared with `_` read as `-` (`Remote_User` is dropped like `Remote-User`). **Cookies are per site**: the
  site's `Set-Cookie` names are kept in the browser as `hp_<slug>_<name>` (§3.2), and only the cookies with this
  site's prefix are forwarded, with the prefix taken off — `ingress_session`, every other cookie of Home Assistant's
  and every other site's never reach it. (`X-Forwarded-Proto`/`-Host` from Home Assistant's ingress are read to know
  whether Home Assistant is on https when the browser sent no Origin/Referer, then dropped.)
- A body sent without a length (chunked) is collected up to an App setting (8 MB) and passed on with its
  `Content-Length`; a bigger one streams on chunked.
- Per site, optional **extra headers** added here (e.g. `Authorization: Basic …`, an API key) — stored server-side,
  never sent to the browser (§4.4).
- `Accept-Encoding` is passed on; responses that are rewritten are decompressed first (§3.2).

### 3.2 Responses (site → browser), rewritten server-side
- **Redirects**: a `Location` on the target's own origin, or starting with `/`, becomes `P/…`. A redirect to
  another host is passed on unchanged (the frame leaves the proxy; the viewer bar offers *Back to sites*).
- **HTML** (`text/html`, up to 5 MB; larger is passed on untouched): root-relative and target-absolute URLs in
  `src`, `href`, `action`, `formaction`, `srcset`, `poster`, `data`, `content` of `<meta http-equiv=refresh>`,
  `<link rel=manifest>`, and inline `style` `url(…)` get `P` in front. The shim (§3.3) is inserted as the first
  `<script>` in `<head>`, before anything of the site's.
- **CSS**: `url(/…)` and `@import "/…"`.
- **JavaScript and JSON are not rewritten** by default (guessing at strings in code breaks things); the shim
  handles what code builds at run time, and per-site rules (§3.5) can rewrite bodies when a site needs it.
- **Headers**: `X-Frame-Options` and CSP `frame-ancestors` are removed (the site is shown in a frame); the rest of
  the site's CSP is kept, with the shim allowed where it lists script sources — `'self'` added to `script-src`
  (the shim is `<ingress>/_hp/shim.js`, on Home Assistant's own origin), the site's nonce used with
  `'strict-dynamic'`, and a policy with only `default-src` gets a `script-src` of its own (default-src's sources plus
  `'self'`) instead of a looser `default-src` — or, per site, the CSP is removed. A `<meta http-equiv=
  Content-Security-Policy>` in the page is cleaned the same way (removed with the per-site option).
  `Set-Cookie`: renamed `hp_<slug>_<name>`, `Domain` removed, `Path` put under `P` (so each site's cookies stay its
  own and never reach Home Assistant or another site; a `__Host-`/`__Secure-` cookie's browser name no longer starts
  with the prefix, so its `Path` under `P` is accepted, and the site gets its own name back).
- Everything that isn't rewritten — images, downloads, video, server-sent events (never buffered, even for a rule),
  partial answers (206, never rewritten), chunked responses — is **streamed** through. A rule's own target (not HTML
  or CSS) is buffered only when it says its length. Gzip answers made of several members are read to the end. At most
  3 rewrites run at once.

### 3.3 The browser shim (`/_hp/shim.js`)
A small script, loaded first in every proxied HTML page, that rewrites URLs the page builds while it runs:

- One rule: a URL that is root-relative (`/x`), on Home Assistant's own origin but not already under the ingress
  path, or on the target's origin → `P/x`. `ws(s)://` URLs on the target's or Home Assistant's host → `wss://<HA
  host>P/x`. Anything else (other hosts, `data:`, `blob:`) is left alone.
- Applied to `fetch`, `XMLHttpRequest.open`, `WebSocket`, `EventSource`, `navigator.sendBeacon`, `window.open`,
  `Worker`/`SharedWorker`, `history.pushState`/`replaceState`, and the `src`/`href`/`action` property setters and
  `setAttribute` on script, link, img, iframe, source, a and form elements — the setters, so the browser never
  starts a request to the wrong place (a bundler's lazily added stylesheet or script included).
- **Storage and cookies per site** (added 0.6.4): `Storage.prototype` (`getItem`, `setItem`, `removeItem`, `key`,
  `length`, `clear`) keeps this site's keys as `hp:<slug>:<key>` (clear() removes only those), and `document.cookie`
  reads and writes only `hp_<slug>_<name>` cookies, on path `P/` (or `P` + the path asked), without `Domain`, and
  without `Secure` when Home Assistant is plain http — the same names the server gives `Set-Cookie`. This stops
  accidents (a site wiping Home Assistant's sign-in with `localStorage.clear()`), not a hostile page (named-property
  storage access, IndexedDB and the Cookie Store API aren't wrapped; DOCS.md says so).
- The wrapped constructors keep `new.target` (`Reflect.construct`), so a page's own subclass of `WebSocket` works.
- It knows `P`, the target's origin, the slug and the site's mode from a small JSON block the server writes next to
  it.
- Known limits (DOCS.md): URLs written as HTML text (`innerHTML`, `document.write`), a worker's own requests, and a
  full navigation to a root address in browsers without the Navigation API.

### 3.4 Single-page app mode (per site, off by default)
Apps with their own client-side router (React Router, Vue Router — Komodo's UI is one) read
`location.pathname` to decide what to show; under `P` they see `/api/hassio_ingress/<token>/s/komodo/stacks` and
show "not found". In **single-page app mode** the shim, before the app starts, changes the frame's address with
`history.replaceState` to the site's own path (`/stacks`), and keeps it that way when the app navigates, so the
app's router sees exactly what it would at its own address. Consequences, said in the form: reloading *inside* the
frame isn't possible (the viewer's ↻ reloads through the proxy instead), and the frame's own address isn't a link
anyone can open (the viewer's *Copy link* gives the deep link, §5.1). Apps that set a base path from `<base href>`
(Angular and others) usually work without this mode.

### 3.5 Per-site rules (advanced)
A list of `{where: body|header, types: [content types], match: regex, replace}`; `$prefix` in `replace` is `P`.
For the site that hard-codes something the layers above miss. The **Test** (§5.3) suggests a rule when it finds a
common pattern (e.g. a `basename:"/"` or `"/api/"` string in the main script).

### 3.6 Websockets
`/s/<site>/…` accepts the upgrade (the Supervisor's ingress passes websockets), opens one to the target (`ws`/`wss`,
same path, subprotocols kept, `Origin` rewritten as §3.1, the site's own cookies only), trying each checked address in
turn, and relays frames both ways until either side closes (pings every 30 s; close codes that may only be observed —
1005, 1006, 1015 — are passed on as 1000). The same access check as every request (§4.2), at the upgrade — and again
while it is open: every 30 s, and at once when a site or a person changes; a person who may no longer open the site
has it closed (1008). At most 16 open per person and site. With the access log on, a line when it opens and one when
it closes (close code, bytes carried, seconds open).

## 4. Security and identity

### 4.1 Who reaches the app
The shared ingress check (`auth_core.refuse_outsiders`: only the Supervisor's ingress proxy) and identity headers,
the admin list (`admin_users` option), as in every app here. `panel_admin: false`: **everyone** sees *Proxy* in
the sidebar, and each person sees and opens only the sites they're allowed on (§4.2). Someone allowed on none sees
"No sites for you yet — ask an admin" (and admins, Admin → Sites). Admin pages and the admin API are admins only.

Before anything else, a **gate** (the outermost ASGI app) checks every connection, plain requests and websockets
alike: the ingress source; a state-changing request the browser labels `Sec-Fetch-Site: cross-site`/`same-site` is
refused **on `/s/…` too** — a proxied site can't defend itself here, because its `Origin`/`Referer` are rewritten to
its own address (§3.1); a websocket whose `Origin` names another host is refused; and (0.6.4) a call to the app's
`/api/admin…`, or any state-changing call to its `/api/…`, whose `Referer` is a proxied page (`<ingress>/s/…`, or
outside the ingress path altogether, as a single-page app mode frame shows) or whose `Sec-Fetch-Dest` isn't `empty`
(a form, a frame) is refused — a speed bump against a proxied page using the admin API, not a hard boundary (§4.3).
The app's own API also limits request bodies (1 MB; a restore 2 GB with its length), counting chunked ones.

### 4.2 Who opens a site
Per site: **Admins only** (the default), **Everyone**, or **Chosen people** (from Home Assistant's people, the
shared people page). Checked on every request to `/s/<site>/…`, on the websocket upgrade (and while it stays open,
§3.6) and on the viewer; the launcher lists only allowed sites. A site the person may not open answers 404, exactly as
one that doesn't exist (no guessing which slugs exist). A person turned off in Admin → People opens none (403, the same
for every slug). At most 48 requests (streams included) per person and site at once (429 beyond).

### 4.3 Trusted sites only (Isolated later)
Proxied pages run **on Home Assistant's own address**. A page there can read what Home Assistant's pages store in
the browser — including the Home Assistant login kept by "Keep me logged in" — and call Home Assistant's API as
the person viewing it. (Every app with ingress is in this position; this app makes it easy to put *other
software's* pages there.) So in the first version **every site is trusted**, and the add form says so in two
sentences with a tick to confirm: *add only software you run and keep updated (Komodo, your NAS) — not a
router, printer or camera page with old firmware.* The confirmation is stored with the site (`trusted_ok_by`,
`trusted_ok_at`). DOCS.md and the README say it plainly (0.6.4): **a flaw in any listed site exposes Home Assistant
for whoever opens it**; add only sites you trust; prefer *Admins only*.

Mitigations (0.6.4), none of them a wall against a page written to get around them: cookies and browser storage per
site (§3.1–§3.3), the admin API refused to proxied pages (§4.1), websockets closed when access is withdrawn (§3.6),
and the form warns about targets on Home Assistant's internal network (`172.30.32.0/23`, `supervisor`,
`homeassistant`) and link-local addresses (allowed, as before: §4.5).

**Later version — Isolated sites** (doubtful: see the note below): served with `Content-Security-Policy: sandbox allow-scripts allow-forms
allow-popups allow-downloads` (no `allow-same-origin`), so the page gets an origin of its own and can read nothing
of Home Assistant's; pages that keep their own login in cookies or browser storage usually can't work that way, and
the Test would say so. Planned in §12. **Note (2026-10-11):** a sandboxed page has an opaque origin, so its own
requests (scripts, styles, API calls) are cross-site to Home Assistant and the browser leaves out Home Assistant's
`ingress_session` cookie (SameSite) — without it the Supervisor refuses them. A sandbox CSP alone therefore breaks
nearly every site; isolation would need a separate address (a tunnel), which is what §12 recommends for untrusted
pages.

### 4.4 Credentials added by the app
Extra headers (§3.1) let a site open already signed in. Stored in the app's database (shown as `••••`, replaced not
read back), never in a response, never logged. The form warns: *everyone allowed on this site acts as that
account.* Optional **Pass the viewer's name** adds `Remote-User`/`X-Forwarded-User` for sites with header sign-in
(only safe when the site accepts that header from this app alone; the form says so).

### 4.5 Targets
- Only `http`/`https`; host and port from the site. Allowed without question: private and link-local addresses,
  single-label names, `*.local`, `*.lan`, `*.home.arpa`, and Home Assistant's internal network. A public address
  needs **Allow an internet address** (so the app isn't used to reach the internet as Home Assistant). The address
  is checked after DNS resolution, at connect time, not only when saved.
- TLS verified by default; **Trust this certificate** (offered by the Test for a self-signed one) stores its
  SHA-256 fingerprint and accepts only that certificate.
- Redirects are never followed server-side.

### 4.6 Logs
No bodies, no header values, no cookies. Per site, an optional access log (time, person, method, path, status,
size; for a websocket an opening line and a closing line with its duration) kept 7 days, off by default. Lines are
buffered and written every 5 seconds (and before the log is read, and at shutdown).

## 5. What people see

### 5.1 Launcher and viewer
- **Launcher** (the panel's home): a tile per allowed site opened in the proxy — icon, name, a status dot when the
  site is checked (§13), and the start path. Opens the viewer. A **Monitor** tab beside it (§13.6).
- **Viewer** (`/v/<site>`): the site filling the panel, with an optional slim bar — *← Sites*, the name, ↻
  (reload through the proxy), *Copy link* (the deep link) and *Open in new tab* (the same viewer, full window).
  Per site **Show the bar** (off suits apps with their own navigation, like Komodo; then a small floating ← stays).
- **Deep links**: `/<sidebar page>/site/<site>[/<path>]` (shared `deeplinks.py` / `deeplink.js`), for Home
  Assistant dashboard buttons ("navigate" action), notifications and the Household Assistant.
- One sidebar entry (an app has one panel). Separate sidebar entries per site are out of scope (§12).

### 5.2 Admin → Sites
Add / edit / delete / reorder. Fields: name, slug (from the name), icon (emoji, or **Use the site's own icon** —
its favicon, fetched by the Test and stored), target address, **start path**, who can open it, the *trusted
software* confirmation (§4.3), **Single-page app mode**, **Show the bar**, TLS (verify / trusted fingerprint), extra headers, *Pass the
viewer's name*, rules (advanced, collapsed), timeout. **Use**: *Open in Proxy* or *Monitor only*; the **Check**
and **Auto-restart** sections (§13). A monitor-only site has only name, icon, address, who sees it, Check and
Auto-restart.

### 5.3 Test
On the form and per site. Fetches the start page through the same pipeline, server-side, and reports in plain
words: reachable or not (and why: refused, timed out, DNS, certificate — with its fingerprint), status and time,
redirects, frame-blocking headers found (removed), how many root paths were rewritten in the HTML, whether the
main scripts contain root-path strings (`"/api/`, `basename`) or a client-side router. Verdict: **Should work** · **Try
single-page app mode** · **Needs a rule** (with the suggested rule) · **Probably won't work through Home Assistant
— use a tunnel** (§12).

### 5.4 App settings (shared settings page)
Request timeout (default 60 s; websockets unlimited), largest upload passed on (default 100 MB; `ingress_stream`),
uploads without a size collected up to (default 8 MB; 0 = always stream), access log retention; for monitoring
(§13): the network reference address (empty by default, §14), the gap between two incidents' restarts (default
10 min, doubling for each further restart within 24 h), the most automatic restarts per day (default 6), check
history kept (default 30 days).

## 6. Manifest

| | |
|---|---|
| App | `slug: household_proxy`, `version: "0.6.5"`, amd64 and aarch64, `startup: application`, `boot: auto` |
| Ingress | `ingress: true`, `ingress_port: 8099`, `ingress_stream: true`, `panel_icon: mdi:web-box`, `panel_title: Proxy`, `panel_admin: false`; no `ports` |
| APIs | `hassio_api: false` (its own sidebar page for deep links comes from `GET /addons/self/info`, which every app may ask with its own token — as the other apps do); `homeassistant_api: true` (§13: the list of switches, `switch.turn_off`/`turn_on` on the switches an admin chose, the sites' binary sensors, notifications) |
| Network | `host_network: false`: the LAN is reached through the Supervisor's network as any app reaches it. A `.local` name Home Assistant's DNS can't resolve needs the IP instead (the Test says so). |
| Options | `admin_users` (as every app); everything else in the app |

## 7. Stack and files
Python 3.12, FastAPI + uvicorn (as the other apps), `httpx` (async, streaming, per-site TLS), `websockets`
(client side), the standard library's HTML tokenizer for the rewrite (fast enough at ≤ 5 MB). Shared code from
`common/` (auth_core, web_security for the app's *own* pages only, settings_core, db_core, people, deeplinks, ui.js,
themes). SQLite `/data/proxy.db`.

```
household_proxy/app/
  main.py         the gate (§4.1), /s/<site> (HTTP + websocket), the app's pages and /api, deep links, jobs
  auth.py         who is asking (identity headers), admins, the short cache for proxied requests
  config.py       options (admin_users), paths, the version, Home Assistant's time zone
  db.py           SQLite schema and migrations, backups
  settings.py     App settings (§5.4)
  sites.py        sites, access, the saved form and its warnings, the in-memory cache
  netguard.py     target checks (§4.5): parsing, address kinds, resolve at connect time, TLS contexts
  proxy.py        one HTTP exchange: request cleaning (§3.1), bodies, streaming, response headers (§3.2), the
                  access log, per-person limits
  rewrite.py      HTML / CSS / Location / Set-Cookie / CSP rewriting, site cookies, rules (§3.5), the shim's tag
  decoding.py     gzip/deflate read in bounded pieces
  wsproxy.py      websocket relay (§3.6)
  probe.py        the Test (§5.3), favicon, certificates
  watchdog.py     checks (§13.2), their schedule, states, incidents, alerts, sensors, history
  restarts.py     auto-restart sequences and their safety rules (§13.3)
  sidebar.py      the app's sidebar page (deep links)
  routers/api.py  the app's API (§9, §13.8)
  static/         index.html, app.js, style.css, _hp/shim.js (+ common/)
```

`web_security`'s CSP and cross-site guard apply to the app's own pages and API, never to `/s/…` responses
(those carry the site's own headers, cleaned as §3.2; Isolated sites, later, the sandbox of §4.3).

## 8. Data model (sketch)
- `sites(id, slug UNIQUE, name, icon, favicon BLOB, target, start_path, access 'admins'|'everyone'|'chosen',
  trusted_ok_by, trusted_ok_at, spa_mode, show_bar, tls_fingerprint, allow_public, timeout_s, position, created_at,
  …the monitoring columns of §13.7)`
- `site_people(site_id, user_id)`, `site_headers(site_id, name, value)` (secret), `site_rules(site_id, position,
  where_, types, match, replace)`, `access_log(site_id, at, user_id, method, path, status, bytes)`.
- `users` as the other apps (people from Home Assistant, enabled switch); `user_notify(user_id, service, created_at,
  created_by)` — extra notify services per person (0.6.5).

## 9. API (sketch)
`GET /api/sites` (the viewer's sites); admin: `GET/POST /api/admin/sites`, `PUT/DELETE /api/admin/sites/{id}`
(the save answers with the site and `warnings`),
`POST /api/admin/sites/reorder`, `POST /api/admin/sites/test` (a site body, not saved) and
`/api/admin/sites/{id}/test`, `GET /api/admin/sites/{id}/log`; monitoring in §13.8; the shared settings, users and
backup routes.

## 10. Tests (plan)
- **Rewriting** (unit): HTML attributes incl. `srcset` and meta refresh, CSS `url()`/`@import`, `Location`,
  `Set-Cookie` path/domain, frame headers, CSP adjustment, rules with `$prefix`, size cap passes through.
- **Proxy** (against a fake site served in the test: a root-path SPA with absolute assets, a JSON API, a
  websocket echo, server-sent events, a redirect, cookies, a 30 MB download): pages, streaming, uploads,
  websockets relayed, the ingress prefix from `X-Ingress-Path`.
- **Security**: outsiders refused; a person not allowed → 403 on pages, websocket upgrade and viewer; no way to
  reach another host (`..`, `//`, encoded tricks, absolute-form request lines); HA headers and cookies never
  reach the site; extra headers never reach the browser; public targets refused unless allowed, also after DNS;
  a site can't be saved without the trusted-software confirmation; a wrong certificate refused, a trusted
  fingerprint accepted; a non-admin sees the launcher with only their sites and gets 403 on the admin API.
- **Shim** (`node --test`): each wrapped API maps URLs by the one rule; other hosts untouched; SPA-mode address.
- **Monitoring and auto-restart**: §13.9.
- **Browser** (Playwright, skipped without it): the fake SPA loads, navigates with its router in SPA mode, its
  websocket and lazily loaded chunks work, all under an ingress-like prefix.
- **By hand on real Home Assistant**: Komodo (sign-in, stacks, logs, a terminal), the Home Assistant apps on
  phones (Android and iOS) and remotely through Home Assistant Cloud.

## 11. Build order
1. The proxy core: sites, access, request/response cleaning, HTML/CSS/header rewriting, launcher and viewer,
   Admin → Sites.
2. The shim, single-page app mode, websockets.
3. Test, favicons, rules.
4. Monitoring (§13): checks, monitor-only sites, the Monitor page, binary sensors, notifications.
5. Auto-restart with its safety rules (§13.3).
6. Komodo and two or three other real sites (and a smart plug) on real hardware; then the README row and a release.

## 12. Alternatives considered, and later
- **A tunnel + a Webpage dashboard** (Cloudflare Tunnel or Tailscale, the site at its own address): the most
  compatible — the site sees its own root — but needs a domain or a VPN and the site's own login. What the Test
  recommends when a site can't work here.
- **hass_ingress** (a community integration via HACS): the same idea, configured in YAML, with body rewrite rules;
  no per-person access, no Test, no single-page app mode, websockets not documented.
- **nginx with `sub_filter`** in the container: static config, rewrites only bodies, no per-person access or Test.
- **A service worker** that catches every request from proxied pages: it can't catch page navigations or
  websockets, and the iOS app's web view supports service workers only in limited cases. Kept as a possible later
  option per site.
- **Later version — Isolated sites** (§4.3): the sandbox CSP, a per-site *Trusted / Isolated* choice in the form
  (Isolated suggested for routers, printers and cameras), the Test checking whether a page needs cookies or browser
  storage, and tests that Isolated responses carry the sandbox.
- **Later**: one sidebar entry per site (would need a custom integration to register panels), a Household
  Assistant tool ("open Komodo", "is the NAS up?"), other restart actions than a switch (a `button` entity, a
  script), restarting a Docker container through Komodo's API instead of the power.

## 13. Monitoring and auto-restart

### 13.1 Proxy or monitor only
Each site has a **Use**: *Open in Proxy* (everything in §3–§5) or *Monitor only* — not proxied (`/s/<site>`
answers 404), shown only on the Monitor page, no trusted-software confirmation needed (nothing of it runs on Home
Assistant's address). A monitor-only site's address can be a URL, a host name or IP alone (ping), or `host:port`.
Both kinds can have checks and auto-restart; who may see a site (§4.2) applies to its status as well.

### 13.2 Checks (per site, off by default)
- **Kind**: **Web page** (default for proxied sites: a `GET` of the start path or a chosen health path, redirects
  not followed, the site's TLS rules; *up* = an answer within the timeout with a status below 500, or only 2xx/3xx
  if chosen), **Port** (a TCP connection to `host:port`), **Ping** (3 ICMP echoes, up if any is answered; sent from
  an unprivileged ICMP socket — if the container doesn't allow it, the Test says so and suggests *Port*).
- **Check every**: default 60 s (15 s – 24 h). **Timeout**: default 5 s (1–60 s).
- **Down after**: default 3 failed checks in a row (1–10), so one lost packet never power-cycles anything. **Up**
  again after one good check.
- Checks are spread over the interval (not all at once), at most 8 at a time, and continue whether or not anyone
  has the page open.
- States: *Not checked* · *Up* · *Down* · *Restarting* · *Waiting after restart* · *Needs attention* (gave up) ·
  *Paused*, each with *since*.

### 13.3 Auto-restart (per site, off by default)
Settings:
- **Switch**: a Home Assistant `switch` entity that powers the device (a smart plug), picked from Home Assistant's
  list (name, current state). The form warns: *never a plug that powers Home Assistant itself, your router or your
  network switch.*
- **Off for**: default **5 s** (1–300 s) — off, wait, on.
- **Wait after restart**: default **120 s** (10 s – 60 min) before checking again, for the device to boot.
- **Restarts before giving up**: default **3** (0 = never restart, only report; up to 10).
- **Only restart between** (optional): a time window, e.g. 02:00–06:00; outside it the site is reported down and
  restarted when the window opens, if still down.

One incident:
1. The site goes **Down** (§13.2) → an incident opens; *down* notification.
2. A restart, if any are left and the safety rules allow (below): `switch.turn_off`; the app waits for the
   entity to report `off` (up to 10 s), waits *Off for*, `switch.turn_on`, waits for `on` (up to 10 s).
   *Restarting* notification ("restart 1 of 3").
3. *Waiting after restart*: no checks until *Wait after restart* has passed. Then one check: up → the incident
   closes (*back up after restart 1*); down → step 2 again.
4. All restarts used → **Needs attention**: *gave up* notification. Checks go on at the normal interval but no
   more restarts in this incident; it closes when the site is up again, or an admin presses **Try again** (the
   count starts over).

Safety rules:
- **Never left off.** Each step is written to the database before it's taken (`restart_phase`,
  `switched_off_at`). At start-up, and when Home Assistant answers again after being away, any switch this app
  turned off and didn't turn back on is turned on first; if that fails it is retried every 30 s for 10 minutes and
  admins are told.
- **Not when the network is the problem.** Before each restart the app checks a **reference** (default: the
  gateway; App settings can name another address). If the reference doesn't answer, or more than half of the
  checked sites went down within the same 2 minutes, no restart happens — *the network looks down, not the
  device* — and the incident waits.
- **A switch Home Assistant can't reach** (`unavailable`/`unknown`) → no restart; *couldn't restart* notification.
- **One sequence per switch**: sites sharing a switch are restarted once, together, and the restart counts for each
  site that is down (or waiting in an incident). Since 0.6.4 every site on the switch is marked when the sequence
  starts: one that was up goes *Restarting* then *Waiting after restart* too (no incident; checked as usual after the
  wait), one that gave up keeps *Needs attention* but isn't checked until it has started; all get `last_restart_at`
  and a `restart_log` row, and all are in the `switched_off` record (never left off covers them). The restart doesn't
  happen while **any** site on the switch is paused (also for Restart now) or outside its window.
- **No restart loops** (0.6.4): after a restart, `down_after` failed checks in a row again before the next one (not
  one); a new incident's first restart waits at least the App settings gap (default 10 min) after the site's last
  restart, twice as long for each further restart within 24 h (at least 5 min from the second, at most 12 h); and at
  most `max_restarts_per_day` automatic restarts per site in 24 h (App setting, default 6; Restart now isn't held
  back, Try again empties the count). Being held back by the daily most is alerted (*couldn't restart*).
- **Check now** while restarting or waiting (or while a scheduled check runs) shows the answer, not recorded; a
  check that started before the latest restart is dropped when it ends.
- Only `switch.turn_off` / `switch.turn_on`, only on the entity an admin chose for that site. Every restart is in
  the site's history (§13.6) and Home Assistant's logbook.
- **By hand** (admins): **Restart now** (asks first; counts in the incident if there is one), **Pause checks** for
  1 h / 8 h / until resumed (for maintenance — no restarts while paused), **Try again** after giving up.

### 13.4 Alerts (who is told, per site)
- **Alert**: per site, the people to alert, picked from Home Assistant's people — **any** people, not only those
  who can open the site (default: every admin; *nobody* is allowed). Each person's phones come from Home Assistant
  (the shared `ha_notify` / `ha_people`, as the other apps); an admin can also add an extra notify service per
  site (a speaker, a group).
- **Which events**, per site: *down*, *restarting* (each restart), *back up* (with how long it was down and how
  many restarts), *gave up*, *couldn't restart* (switch unavailable, network looks down). Default: all but
  *restarting*.
- One *down* per incident; nothing repeated while it stays down, except *gave up* once at the end.
- Being alerted doesn't give access: the message names the site and the event; tapping it opens the Monitor page
  at that site (deep link `/monitor/<site>`), which shows the site only to people allowed to see it (§4.2).
- Each person can mute a site's alerts for themselves (Monitor page → 🔕), without changing the site.
- (0.6.5) Each person's alerts go to their phones **and** the extra notify services an admin adds under Admin →
  People → 🔔 (`user_notify`; the shared `people_admin.py` / `people.js`, with **Send a test**, as the other apps).
  A saved site's form has **Send a test alert** (`POST /api/admin/sites/{id}/alert-test`): to exactly the services a
  real alert would use, the results shown per service.

### 13.5 Home Assistant entities (per site, on by default when checks are on)
`binary_sensor.household_proxy_<slug>` (`device_class: connectivity`): on while up. Attributes: `status` (the state
of §13.2), `since`, `last_check`, `response_ms`, `restarts` (this incident), `last_restart`. For dashboards and
automations of your own. Pushed on each change and every 5 minutes (shared sensor publisher); removed when the site
or its checks are.

### 13.6 What people see
- **Monitor page** (`/monitor`): every checked site the person may see, proxied and monitor-only — icon, name,
  state with *since*, response time, a 24-hour strip of up/down, restarts in the current incident. Admins also see
  **Restart now**, **Pause**, **Try again**.
- **Site history** (admins): incidents for 90 days — start, end, cause (timed out, refused, status 502, no ping
  reply), restarts, outcome — and uptime over 24 h, 7 and 30 days.
- **Admin → Sites form**: *Use*, a **Check** section (kind, path, every, timeout, down after, accepted statuses) and
  an **Auto-restart** section (switch, off for, wait after, restarts, window, sensor), and an **Alerts** section
(people, extra service, events). **Check
  now** runs one check; the switch row shows the switch's current state (the form never toggles it — that's
  *Restart now*).

### 13.7 Data
- `sites` gains: `use 'proxy'|'monitor'`, `check_kind 'http'|'tcp'|'ping'|NULL`, `check_path`, `check_every_s`,
  `check_timeout_s`, `down_after`, `ok_codes 'below500'|'2xx3xx'`, `restart_entity`, `off_for_s`, `wait_after_s`,
  `max_restarts`, `window_start`, `window_end`, `alert_service` (extra notify service), `alert_events` (JSON),
  `expose_sensor`, `paused_until`, `state`, `state_since`, `fails_in_row`, `last_check`, `last_ms`, `incident_id`,
  `restart_phase` ('off'|'on'|NULL), `switched_off_at`, `next_check_at`.
- `incidents(id, site_id, started_at, ended_at, cause, restarts, outcome 'up'|'up_after_restart'|'gave_up'|
  'deleted')` kept 90 days; `checks(site_id, at, ok, ms, error)` kept per App settings (default 30 days, hourly
  summaries after 7); `site_alert_people(site_id, user_id)` (filled with the admins when a site is added),
  `alert_mutes(site_id, user_id)`.

### 13.8 API
`GET /api/monitor` (the viewer's checked sites and their state); admin: `GET /api/admin/sites/{id}/incidents`,
`POST /api/admin/sites/{id}/check-now|restart|pause|resume|retry`, `GET /api/admin/switches` (Home Assistant's
`switch` entities with name and state).

### 13.9 Tests (plan)
With a fake clock and the shared fake Home Assistant recording service calls: down only after *N* failures; off →
*Off for* → on with the configured times; nothing checked during *Wait after restart*; up after a restart closes the
incident; gives up after *K* restarts and restarts again only after being up or *Try again*; **a crash between off
and on → on at start-up**; the network guard (reference down; most sites down together); an unavailable switch;
two sites on one switch → one sequence, the sibling that was up marked too, a paused or out-of-window sibling holds it
back, a sibling that gave up keeps its state, never-left-off covers every site on the switch; `down_after` failures
again after a restart; the daily most and the growing gap with a flapping site; Check now during the wait and a check
started before a restart not recorded; the time window; the gap between incidents; pause; manual restart is
admins-only; alerts go to exactly the site's chosen people (not people who merely can see it), per event, and
not to someone who muted it; an alerted person who can't see the site gets no access from the link; the binary sensor's state and attributes; a monitor-only site
is never proxied and is shown only to the people allowed; each check kind (web page statuses, port, ping refused by
the container).

## 14. Decisions
- 2026-10-13 (independent review, 0.6.4): the shared origin stays (§4.3) and is said plainly in DOCS and README;
  mitigations, none a hard boundary: per-site cookie names (`hp_<slug>_<name>`, only those forwarded — existing
  sites' cookies were re-issued, so sites may ask people to sign in once), per-site Storage keys in the shim, the
  admin API and API writes refused from proxied pages (Referer / Sec-Fetch-Dest), websockets re-checked every 30 s and
  on changes, 404 for sites a person may not open, `_` read as `-` in header names, per-person limits (48 requests,
  16 websockets per site), rule expressions with a repeat inside an unlimited repeat refused, warnings for targets on
  Home Assistant's internal network or link-local. Auto-restart: siblings on a switch marked and respected (pause,
  window), `down_after` again after a restart, daily most (6) and a doubling gap, Check now guarded. The httpx
  transport's address check is attached and verified (the app refuses to start a transport without it). The SSH
  terminal (`spec/SSH_SPEC.md`) stays on hold.
- 2026-10-12: an SSH terminal (Termius-style: admin-set connections, per-person access) is specced in
  `spec/SSH_SPEC.md`, not built.
- 2026-10-10: name **Household Proxy** (`household_proxy`), sidebar title *Proxy*.
- 2026-10-10: everyone sees the panel; each person sees and opens only the sites they're allowed on (§4.1, §4.2).
- 2026-10-10: Isolated sites are a later version; the first is trusted sites only, with the confirmation (§4.3, §12).
- 2026-10-11: checks and auto-restart by a Home Assistant switch, per site: check interval, wait after restart and
  restarts before giving up all configurable (§13.2, §13.3); a site can be *monitor only* (§13.1); who is
  alerted is chosen per site (§13.4).
- 2026-10-11 (security review before building): the cross-site check also covers `/s/…` and websockets (§4.1);
  `hassio_api: false` (§6); loopback and "this host" addresses are never targets (the app's own port is on
  loopback); the address is checked at connect time and the checked address is the one connected to (§4.5);
  `Strict-Transport-Security`, `Clear-Site-Data`, `Alt-Svc`, `NEL`/`Report-To` and `Service-Worker-Allowed` from a
  site are dropped (they would act on Home Assistant's own address); proxied pages get `frame-ancestors 'self'`; the
  Isolated mode of §4.3 is noted as unlikely to work through ingress.
- 2026-10-11 (building §13): the network reference is **empty by default** (inside an app's container the default
  route is the Supervisor's own network, which always answers — so "the gateway" would never catch a network
  outage); an admin sets the router's address in App settings. The "more than half of the checked sites went down
  together" rule applies only when at least 3 sites are checked (with one or two it would block every restart).
  Choosing a switch needs a confirmation tick that it doesn't power Home Assistant, the router or the network switch
  (stored with who and when), not only a warning. A failed turn-on after 10 minutes also tells every admin directly.
