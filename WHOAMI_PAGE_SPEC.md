# "How the app sees you" and "No admin yet": the shared spec

Every household app decides who is an administrator from its own `admin_users` option, and every app
has the same small read-only page, **How the app sees you**, that shows exactly which user name and
user id Home Assistant sent, whether the app treats the person as an administrator and, if not, exactly
what to type into the app's Configuration tab. While `admin_users` is empty every page also shows the
**No admin yet** banner.

Both are built from shared code:

| Piece | File (in `common/`) | Copied to | Used by |
|---|---|---|---|
| The data | `python/whoami.py` | `app/common/whoami.py` | all 9 apps |
| The page and the banner (plain-JS apps) | `static/whoami.js` | `app/static/common/whoami.js` | all but Finance |
| Contract tests | `tests/test_whoami_shared.py` | `tests/common_tests/` | all 9 apps |

Finance Dashboard draws the page on the server (Jinja): it uses `whoami.py` for the data and its own
template, with the same rows and wording.

---

## 1. Why every app with an admin list wants this

An app that reads its administrators from its options produces one support request over and over:
**"I added my name in the configuration but I don't see the admin buttons."** The cause is almost
always one of three things, and none of them can be seen from inside the app without this page:

1. the app was **not restarted** after the list changed (options are read when the process starts);
2. the string in the list is **not the string Home Assistant sends** (a display name instead of the
   login name, a typo);
3. the person is not signed in as who they think (another HA user, a shared tablet).

---

## 2. The contract (`whoami.build`)

Each app keeps its own route — `GET /api/whoami` (Receipt Price Intelligence: `GET /api/v1/whoami`;
Finance: the server-rendered `GET /whoami` page) — and its own idea of who the caller is. The route
calls `whoami.build()` with what it already knows:

```python
from .common import whoami as whoami_core          # ..common in app/routers/

@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    linked = bool(ha_notify.services_for(user))
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_NAMES),
        display_name_only=user["display_name_only"],        # optional, default False
        notify_linked=linked,                               # optional; leave out when the app has no notifications
        extras=[whoami_core.row("Chats you're in", n), whoami_core.notify_row(linked)],
        disabled=user["disabled"])                          # the app's own fields, for its own code
```

The JSON:

| Field | Type | Meaning |
|---|---|---|
| `haUserId` | string | `X-Remote-User-Id` as sent |
| `haUsername` | string or null | `X-Remote-User-Name` (the **login name**); null when Home Assistant didn't send it |
| `haDisplayName` | string or null | the display name (never used for matching) |
| `nameSent` | bool | the login-name header was there |
| `isAdmin` | bool | the id or the login name is on `admin_users` (any case) |
| `displayNameOnly` | bool | not an admin, but the **display name** is on the list (which never counts) |
| `adminEntries` | int | how many names `admin_users` has — the count, never the names |
| `noAdmin` | bool | `adminEntries == 0`: nobody can be an administrator yet (the banner) |
| `viaIngress` | bool | the request carried `X-Ingress-Path` (diagnostic; not drawn) |
| `notifyLinked` | bool | a phone or notify service reaches the person — **only in apps with notifications** |
| `extras` | list | the app's own rows (below) |

Apps may add **their own top-level fields** for their own code (Household Todo: `actingAs`,
`notifyEntries`, `maintenance`, `driveTimes`; Splitpot: `userId` — the Splitpot person —,
`sensorSyncEnabled`, `haConnected`; Family Tree, Chat, Vault: `disabled`; Vault: `status`).
`build()` refuses a field that reuses a shared name.

The same flag drives the banner from the app's start-up call, so it is called **`noAdmin`
everywhere**: in `/api/whoami` and in each app's `/api/me` (Receipt: `/api/v1/me`).

### Extras: the app's own rows

`whoami.row(label, value, hint=None, tone=None, action=None)` makes one row:

- `value`: text or a number (shown as is), a bool (**Yes** / **No**) or None ("—"). Anything else
  (a list, a dict) is refused.
- `hint`: one sentence shown under the table — how to change this row's answer.
- `tone="danger"`: the value is drawn in the danger colour.
- `action={"label", "target"}`: a link button in the value cell; the page passes `target` to the app's
  `onAction` hook (Family Tree: "This is me — not set — set it" opens Settings).

`whoami.notify_row(linked, label=..., hint=...)` is the usual "is a phone linked" row (Yes / No, with a
how-to-link hint only when it isn't).

**Rules for extras: counts and statuses only.** Never another person's name, never the admin list,
never a secret (a password, a token, a vault's contents), never the request's headers. The person's
own things are fine (Family Tree shows the tree person they said is them).

### Rules for every whoami route

1. It reports the **real** signed-in person, even while an admin is "acting as" someone else.
2. It works for people an admin has turned off (Family Tree, Chat, Vault) and, in Vault, while the vault
   is locked: it needs no session and holds nothing from any vault.
3. No `X-Ingress-Path` / no user means 401 (or 403 from an app's ingress check), like every other route.
4. Read-only: no writes, no side effects.

---

## 3. The page (`whoami.js`)

Loaded with the app's other scripts (`<script src="common/whoami.js?v=X.Y.Z">`, before the app's own
scripts). It draws the page from the JSON with DOM nodes and `textContent` only — the names come from
request headers and are never parsed as HTML — and uses the app's own CSS classes:

```js
const body = HouseholdWhoami.panel(w, {
  appName: "Household Todo",            // for "(Settings → Apps → Household Todo → Information → Restart)"
  classes: { kv: "kv", row: "kv-row", label: "kv-label", value: "kv-value", copy: "icon-btn",
             advice: "hint", hint: "hint" },   // the defaults; row: null = label and value straight in .kv (a grid)
  adviceTag: "div", adviceStyle: "margin-top:8px",
  copyGlyph: "⧉",
  onCopy: (text, button) => copyText(text),   // default: clipboard, then ✓ on the button for a moment
  onAction: (target) => go(target),           // an extras row's action button
});
```

The app wraps it in its own card and heading ("How the app sees you"). What it shows:

| Row | Value |
|---|---|
| User name (sent by Home Assistant) | the login name, or "not sent"; a copy button |
| User id (sent by Home Assistant) | the id (monospace); a copy button |
| Display name (not used for matching) | the display name, or "not sent" |
| Administrator in this app | **Yes** / **No** |
| Names in the app's admin_users | the count |
| *the app's extras* | … |

Then one sentence of advice — four cases — and each extras row's hint:

1. **Admin:** "You are an administrator."
2. **Display name only:** "Your **display name** is in the `admin_users` list, but display names aren't
   accepted there (anyone could share or take a name). Replace it with **jane.doe** (or **9f3c…**),
   save, and **restart** the app."
3. **Empty list:** "The `admin_users` list is **empty** in the running app, so nobody is an
   administrator yet. Add **jane.doe** to `admin_users` on the app's Configuration tab, save, and
   **restart** the app (Settings → Apps → *App* → Information → Restart) — the list is only read when the
   app starts, so if you have already filled it in, a restart is all it needs."
4. **No match:** "Neither your user name nor your user id above matches any of the *N* names in the
   `admin_users` list. Add **jane.doe** (or **9f3c…**) exactly as shown, save, and **restart** the app —
   the list is only read when the app starts. Upper and lower case don't matter."

The name to add is the login name when Home Assistant sent one, else the user id ("(or …)" is left out
then).

**Reachable from everywhere:** the signed-in name in the sidebar is a link or button to the page, and
on phones (no sidebar) a 👤 button or the Settings page leads there. Links are relative (`whoami`,
`whoami.html`, `#/whoami`), never `/whoami`.

---

## 4. The "No admin yet" banner

While `noAdmin` is true, every page shows, to everyone, at the top of the content (each app's existing
banner place, above the pages):

> **No admin yet** — add your Home Assistant user name (**jane.doe**) to `admin_users` on the app's
> Configuration tab, save, and restart the app. [How the app sees you]

```js
HouseholdWhoami.fillNoAdminBanner(container, me.noAdmin, me.username || me.id,
                                  { onOpen: openWhoami });      // or { href: "whoami.html" }
// or, to place it yourself:
container.append(HouseholdWhoami.noAdminBanner(name, { onOpen }));
```

Nobody is ever made an administrator automatically, not even the first visitor. Finance draws the same
text in `base.html` and adds one sentence of its own (the AI set-up step).

---

## 5. Tests

- **Shared** (`common/tests/test_whoami_shared.py`, run in every app): the fields, `noAdmin` follows the
  count, name not sent, `viaIngress`, `displayNameOnly` never for admins, `notifyLinked` only when given,
  extras rows (values, hints, tone, actions), rows refuse lists and bad shapes, app fields can't shadow
  shared ones, and the page script's wording and DOM-only rendering.
- **Each app** keeps its own: admin by id or login name in any case, display names never match,
  the count and never the list, the real caller while acting as someone else, disabled people still get
  the page, the `noAdmin` flag in `/api/me` and `/api/whoami`, the banner container and script on the
  page, and the app's extras.
- **Finance**: `tests/test_whoami.py` (the server-rendered page).

---

## 6. Each app's extra rows

| App | Route | Extra rows | Own top-level fields |
|---|---|---|---|
| Calorie Tracker | `/api/whoami` | — | — |
| Family Tree | `/api/whoami` | This is me (your tree person, or a "set it" button) · Reminders (on/off · phones · people with 🔔; shown while Reminders is on, and always to someone turned off) · Account status | `disabled` |
| Finance Dashboard | `/whoami` (page) | — | — |
| Household Arcade | `/api/whoami` | Phone linked for notifications | — |
| Household Chat | `/api/whoami` | Access · Chats you're in · Phone linked for notifications | `disabled` |
| Household Docs | `/api/whoami` | Access · Your folder · Files in your folder · Phone linked for notifications | `disabled` |
| Household Todo | `/api/whoami` | Reminder service linked | `actingAs`, `notifyEntries`, `maintenance`, `driveTimes` |
| Household Vault | `/api/whoami` | Set up · Vaults you can open · Account status · Phone linked for alerts | `status`, `disabled` |
| Receipt Price Intelligence | `/api/v1/whoami` (page `whoami.html`) | — | — |
| Splitpot | `/api/whoami` | — | `userId`, `sensorSyncEnabled`, `haConnected` |

---

## 7. Gotchas

- **The app must be restarted after `admin_users` changes** — options are read when it starts. The page
  says so in every non-admin case.
- **`X-Remote-User-Name` is the login name**, not necessarily the display name on the profile page. The
  page shows exactly what arrived.
- **Never match on anything a person can edit themselves.** Display names are never matched; the page
  explains that when only a display name is listed.
- **Keep the count, drop the list.** Showing the names would tell everyone who the administrators are.
- **This page is a diagnostic, not a security boundary.** Admin-only routes keep their own checks.
