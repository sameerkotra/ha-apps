# Household Proxy — Terminals (SSH) — spec

Status: **spec only, not built** (2026-10-12). An addition to Household Proxy (`spec/SPEC.md`): an SSH terminal in
the browser, in the spirit of Termius. An admin sets up SSH connections and chooses who may open each one. Nothing
here changes how proxied sites work.

## 1. What it is, and what it is not

- **A terminal for chosen people on chosen machines.** An admin adds a *connection* (a name, a host, a port, the SSH
  account to sign in as and how to sign in) and chooses who may open it. Those people see it under **Terminals** and
  get a full terminal in the browser, inside Home Assistant, from anywhere Home Assistant can be reached.
- **The browser never holds the SSH credentials.** The app signs in to the machine. The browser only exchanges
  keystrokes and screen output with the app over a websocket.
- **The terminal page is the app's own page.** Unlike a proxied site, no third-party page is involved: the app ships
  xterm.js, under its own strict Content-Security-Policy.
- **Not a jump box for anything else.** The app only asks for a shell. Port forwarding, agent forwarding, X11, SFTP
  and running commands outside the shell are all off, and the app never accepts a channel the server tries to open.
- **Not for watching other people's sessions.** Keystrokes and output are not recorded (§4.7).
- **Not a replacement for Home Assistant's own Terminal app.** That one is a shell on Home Assistant itself. This one
  is for the other machines on the network: the NAS, the Komodo host, a Raspberry Pi.

## 2. How a session flows

```
browser (xterm.js) ──wss──▶ Home Assistant /api/hassio_ingress/<token>/t/nas
                              │  HA sign-in checked; Supervisor ingress passes websockets
                              ▼
                        household_proxy :8099   /t/nas
                              │  gate (§4.1) → may this person open "nas"? → limits (§4.6)
                              │  address checked (netguard, as for sites), host key checked against the pinned one
                              ▼
                        ssh admin@192.168.0.20:22   (asyncssh: one session channel, a PTY, a shell)
```

Messages on the websocket:
- **Browser → app:** binary frames carry keystrokes (≤ 64 KB each). Text frames carry control messages as JSON:
  `{"t":"resize","cols":120,"rows":40}`, with cols 10–500 and rows 5–200, and `{"t":"ping"}`.
- **App → browser:** binary frames carry terminal output. Text frames carry status as JSON: `{"t":"ready"}`,
  `{"t":"closed","why":"idle"|"ended"|"revoked"|"limit"|"hostkey"|"auth"|"unreachable"|"error","msg":"…"}`.
- **Back-pressure:** when the browser falls behind, the app stops reading from SSH (asyncssh's `pause_reading`)
  until the send buffer drains, so `cat` on a huge file can't fill the app's memory.

Paths:

| Path | What |
|---|---|
| `/terminals` | the list of connections this person may open (tiles, like the launcher) |
| `/term/<slug>` | the terminal page |
| `/t/<slug>` | the websocket (the app's own path, not under `/s/`) |
| `/api/ssh/…`, `/api/admin/ssh/…` | the API (§8) |

## 3. Signing in to the machine

How the app signs in is set per connection. They are listed best first.

1. **A key made by the app (recommended, the default).** When the connection is added, the app makes an Ed25519 key
   pair and shows a ready-to-paste `authorized_keys` line for the machine:
   ```
   from="<Home Assistant's address>",no-port-forwarding,no-agent-forwarding,no-X11-forwarding ssh-ed25519 AAAA… household-proxy:nas
   ```
   The private key never leaves the app. There is no way to download or view it. Admins can **Make a new key**,
   which ends any open sessions on that connection and replaces the key.
2. **Ask each time.** The app stores nothing. The terminal asks the person for the account's password, or a
   keyboard-interactive answer such as a one-time code, at every connect. The answer goes straight to SSH and is
   never stored or logged. This is the right choice when each person has their own account on the machine: the
   connection's *account* field can be **the person's own name** (their Home Assistant username) or a fixed name.
3. **A stored password (discouraged).** The password is kept like a site's extra headers: shown as `••••`, replaced
   but never read back, and never in a response or a log. The form says that a key is safer.

A key pasted in by the admin is left out of v1, because it is usually a personal key reused elsewhere. The app's
own key is single-purpose and can be revoked on the machine alone.

**Host key (pinned, never "accept anything").** **Test** connects once and shows the machine's host key type and
SHA-256 fingerprint, in the same format `ssh-keygen -lf` prints. The admin compares it with the machine and ticks
**This is the right machine**. The key is then pinned. From then on, a different key refuses the connection with
*"The machine's identity changed — if you didn't reinstall it, someone may be in between."* Only an admin can accept
the new key, again with **Test**.

## 4. Security

### 4.1 Who reaches it
The same gate as everything else in the app (`spec/SPEC.md` §4.1):
- the request must come through the Supervisor's ingress;
- identity comes from the ingress headers;
- a websocket whose `Origin` names another host is refused.

Admin pages and the admin API are for admins only.

### 4.2 Who opens a connection
Each connection is **Admins only** (the default) or **Chosen people**. There is deliberately **no Everyone**,
because a shell is not something to hand out by default. Access is checked:
- when the terminal page loads;
- at the websocket upgrade;
- **again while the session is open**: when an admin removes someone, turns them off in Admin → People, deletes the
  connection or makes a new key, their open sessions end within a few seconds with `revoked`. Chat taught us to
  check this; see the 2026-10-11 review.

### 4.3 What a person can do on the machine
Whatever the SSH account can do. So the add form says:
- *use an account made for this, not root;*
- *everyone you allow acts as this account;*
- *the `from=` and `no-…-forwarding` options in the key line limit where the key works.*

For several people on one machine, *Ask each time* with each person's own account gives each person their own
rights and their own entries in the machine's logs.

### 4.4 The one risk that can't be fully closed: other pages on Home Assistant's address
Proxied sites (`spec/SPEC.md` §4.3), every other ingress app and Home Assistant itself all run on the **same browser
origin** as this terminal page. A malicious or compromised page there, such as an out-of-date router page added as a
site, runs as the person viewing it. It could open `/t/<slug>` too, or read the terminal page. No header tells the
two apart: the websocket handshake looks the same. The same is already true of Home Assistant's own Terminal app
and of a Komodo terminal opened through the proxy.

What the spec does about it:
- **Only people with SSH access are exposed.** Others get nothing from `/t/…`.
- **A session start is announced.** Each connection has **Tell when someone connects**, on by default: a Home
  Assistant notification goes to the admins, and optionally to the person, saying *"Ann opened a terminal on NAS
  (from the Proxy app)"*. An unexpected session gets noticed.
- **A one-time ticket per session.** The terminal page asks for a ticket (`POST /api/ssh/<slug>/ticket`, a
  state-changing request, so the cross-site check applies) and opens `/t/<slug>?ticket=…` within 30 seconds; the ticket works once. This doesn't
  stop a same-origin page that is determined to get in, but it stops plain drive-by websocket attempts and leaves a
  clear trail in the session log.
- **The add-site form says it.** It gains a line for admins who have terminals: *pages you add here can act as
  anyone viewing them — including opening their terminals.*
- **Ask each time** connections need a secret the attacking page doesn't have.

### 4.5 Where the secrets live
- Private keys and stored passwords are kept in `/data/ssh.db`, a separate database. The app's own **Download a
  backup** leaves it out, as it already leaves out header values.
- Home Assistant's own backups include all of `/data`, so they include the keys. The Storage tab says so. If a backup
  is lost, **Make a new key** on each connection.
- Encrypting the keys at rest would mean keeping the encryption key next to them, so v1 doesn't pretend to.
- The keys are never sent to the browser, never logged and never on the app bus.

### 4.6 Limits

| Limit | Default | Range |
|---|---|---|
| Idle time before the session closes (no keystrokes) | 30 min | 5 min – 8 h |
| Longest session | 12 h | 1–24 h |
| Open sessions per person | 4 | — |
| Open sessions across the app | 16 | — |
| Failed sign-ins per connection before a 5-minute pause | 5 | — |

- The idle time and longest session are set per connection.
- Keystroke frames are capped at 64 KB.
- Connect times out after 15 seconds.

### 4.7 Logs
- **Session log, kept 90 days:** who, which connection, start, end, how it ended, and bytes each way.
- **Never** keystrokes, output, passwords or answers to prompts.
- Recording sessions is left out on purpose. A recording would capture every password typed into `sudo`.

### 4.8 Targets
The same rules as sites (`spec/SPEC.md` §4.5): private addresses and local names are allowed without question, and a
public address needs **Allow an internet address**. Loopback and "this host" are never targets. The address is
checked at connect time, and the checked address is the one connected to.

### 4.9 SSH settings
- **Algorithms:** asyncssh's defaults, minus SHA-1 signatures, `diffie-hellman-group1`, CBC ciphers and `ssh-dss`. The
  Test reports a machine that offers nothing else as **too old to connect safely**.
- **Requests:** only `pty-req` (`xterm-256color`), `shell` and `window-change`. Environment variables are not passed.
- **Server-opened channels** are refused (no reverse forwarding).
- **Keep-alive:** every 30 seconds, ending the session after 3 missed replies.

### 4.10 Honest summary
**About as secure as the Home Assistant sign-ins of the people you allow.** Anyone who gets into one of those
accounts gets the terminals that person may open. For anyone granted a terminal, turn on Home Assistant's two-factor
sign-in. The app can't check that it is on, so the access form reminds admins.

The other links in the chain are:
- the SSH account's own rights, so not root;
- the `from=` line, so a stolen key is useless elsewhere;
- the pinned host key, so no one can pretend to be the machine;
- the connect notification, so a session no one expected gets seen.

It adds **no** open port and no new way in from the internet.

## 5. What people see

### 5.1 Terminals tab
The tab appears next to Sites and Monitor, for people who may open at least one connection. Each connection has a
tile with:
- its name and icon;
- `user@host`;
- a dot showing whether the machine answered the last check, when the connection has the site checks of
  `spec/SPEC.md` §13.

### 5.2 Terminal page
- **Layout:** xterm.js fills the page, with a slim top bar like the viewer's bar. The bar has:
  - **← Terminals**;
  - the connection's name;
  - **Reconnect**;
  - font size − and +;
  - **Copy** and **Paste**.
- **Phones:** a row of extra keys above the keyboard: Esc, Tab, Ctrl (sticky), Alt, the arrow keys, `|`, `~`, `/`
  and `-`. A phone can't use a terminal without them.
- **Ask each time** connections ask for the password in the terminal itself, the way `ssh` does, with no separate
  form.
- **When a session ends,** a line says why (*Closed after 30 minutes idle*, *Your access was removed*, …) with
  **Reconnect** where that makes sense.
- **Several sessions** run in separate browser tabs. Tabs inside the page are left for later.
- **Look:** the theme follows the app's theme. The font is the system monospace font (no font files).

### 5.3 Admin → Terminals
Each connection has these settings:
- name, icon, host, port and account (a fixed name, or *the person's own name*);
- how to sign in (§3);
- the **key line** to copy;
- **Test** (reachable → host key to confirm → signs in → `echo ok`);
- who may open it;
- **Tell when someone connects**;
- idle time and longest session.

The tab also has:
- **Open sessions:** who, which connection, since when, and an **End** button;
- **Session log.**

### 5.4 Home Assistant entities
- `sensor.household_proxy_terminals_open` gives the number of open sessions, with attributes listing who is on what.
  It is off by default.

## 6. Stack and files

| What | Notes |
|---|---|
| `asyncssh` 2.24.x | Pure Python and needs only `cryptography`, which is already in the image. Pinned like everything else. |
| xterm.js 6.0 + `addon-fit` 0.11 | Vendored under `static/vendor/xterm/` with their MIT licences, about 300 KB. No CDN. |
| `app/ssh.py` | Connections, keys, host-key checks, the session bridge, limits. |
| `app/routers/ssh.py` | The API and the `/t/<slug>` websocket. |
| `static/term.html`, `static/term.js` | The terminal page and the mobile key row. |
| `static/app.js` | The Terminals tab and Admin → Terminals. |

The terminal page's CSP:
- `script-src 'self'`;
- `connect-src 'self'`;
- `frame-ancestors 'self'`;
- no inline scripts.

## 7. Data

```
ssh_connections(id, slug, name, icon, host, port, account_kind['fixed'|'own'], account, auth['app_key'|'ask'|'password'],
                public_key, host_key_type, host_key_fp, host_key_ok_by, host_key_ok_at, allow_public,
                access['admins'|'people'], notify_on_open, idle_min, max_hours, created_by, created_at)
ssh_people(connection_id, user_id)
ssh_sessions(id, connection_id, user_id, started_at, ended_at, end_reason, bytes_in, bytes_out)
-- /data/ssh.db (separate, left out of the app's own backup download):
ssh_secrets(connection_id, private_key, password)
ssh_tickets(ticket_hash, connection_id, user_id, expires_at)   -- or in memory
```

## 8. API (sketch)

| Method and path | What |
|---|---|
| `GET /api/ssh` | Connections this person may open |
| `POST /api/ssh/<slug>/ticket` | A one-time ticket (30 s) |
| `WS /t/<slug>?ticket=…` | The session |
| `GET/POST/PATCH/DELETE /api/admin/ssh[/<id>]` | Connections (secrets write-only) |
| `POST /api/admin/ssh/<id>/test` | Host key + sign-in check |
| `POST /api/admin/ssh/<id>/accept-host-key` | Pins the key the Test showed (its fingerprint must be sent back) |
| `POST /api/admin/ssh/<id>/new-key` | Makes a new key pair (ends sessions) |
| `GET /api/admin/ssh/sessions` | Open sessions and the log |
| `POST /api/admin/ssh/sessions/<id>/end` | Ends a session |

## 9. Tests (plan)

Run against an in-process asyncssh server, as `fakesite.py` is used for sites.

- **Sign-in:** each way of signing in. The app's key works, and the `authorized_keys` line it shows is valid and
  carries the restrictions.
- **Host key:** an unpinned host key is refused, and a changed key is refused with `hostkey`. Accepting needs the
  matching fingerprint.
- **Access:**
  - checked at page load, ticket and upgrade;
  - a ticket works once and expires;
  - a websocket from another origin is refused;
  - removing a person, turning them off, deleting the connection or making a new key ends their session.
- **SSH requests:**
  - only `pty-req`, `shell` and `window-change` are sent;
  - a port-forward or agent request from the server is refused;
  - an exec or subsystem can't be asked for.
- **Limits:** idle and longest session, per person and app-wide caps, the failed sign-in pause, and the frame size cap.
- **Back-pressure:** 50 MB of output to a slow reader keeps the app's memory bounded.
- **Resize:** out-of-range sizes are clamped.
- **Secrets:**
  - never in an API response, the app's backup download or a log line (grep the captured logs);
  - an *Ask each time* password is never written anywhere.
- **Targets:** the netguard rules, including loopback refused and a public address needing the tick.
- **Notifications:** the notification on connect, and the session log entries.
- **Browser (Playwright):**
  - typing reaches the shell and output appears;
  - resize reaches the PTY;
  - the mobile key row sends Ctrl-C;
  - on reconnect the page needs a fresh ticket.

## 10. Build order

1. **S1 — Core:**
   - the data model, Admin → Terminals with Test, host-key pinning and the app's own key;
   - the session bridge with limits and access re-checks;
   - the terminal page (desktop).
2. **S2 — Everyday use:**
   - *Ask each time* (including keyboard-interactive prompts) and the stored password;
   - the mobile key row;
   - notifications, Open sessions with End, the session log, the sensor.
3. **S3 — Later, if wanted:**
   - tabs inside the terminal page;
   - SFTP file browser (download and upload), off per connection by default;
   - a jump host (ProxyJump);
   - per-person keys;
   - snippets (saved commands).

**Effort:** S1 and S2 are about the size of the proxy's websockets phase. That is roughly 1,500–2,000 lines with
tests, most of them in tests and the admin page. The SSH side is small because asyncssh does the protocol. The
careful parts are:
- the access re-check while a session is open;
- back-pressure;
- making sure the app never asks for or accepts anything but a shell.

## 11. Alternatives considered

- **Proxy an existing web terminal** (ttyd, Wetty, Sshwifty) as an ordinary site. This needs no code today, since
  websockets already work, but:
  - the credentials sit in that tool;
  - access is all or nothing per site;
  - its page runs on Home Assistant's origin as a trusted site;
  - there is no session log, connect notification or per-person account.

  It works as a stopgap.
- **A separate app just for terminals.** It would still be on Home Assistant's origin (every ingress app is), so §4.4
  would not change. It would duplicate the people, admin and notification parts. Keeping it in Proxy reuses the
  access model, netguard and the Monitor checks.
- **Browser-side SSH (WASM).** The key would have to be in the browser, which defeats §3.

## 12. Decisions (to confirm)
- Inside Household Proxy, as a **Terminals** tab; not a separate app.
- No *Everyone* access for a terminal.
- Default sign-in: a key made by the app, with the restricted `authorized_keys` line.
- Host key pinned at **Test**; a changed key is refused.
- **Tell when someone connects**: on by default.
- No session recording.
