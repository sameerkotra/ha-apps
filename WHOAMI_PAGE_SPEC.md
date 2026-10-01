# "How the app sees you" page: build spec for a Home Assistant ingress app

A small read-only page (`/whoami`) that tells the person looking at it exactly which
**user name** and **user id** Home Assistant sent to the app, whether the app treats them
as an **administrator**, and, if not, exactly what to type into the app's Configuration tab.

Reference implementation: Finance Dashboard app, version 0.4.63 (`app/auth.py`,
`app/routes/accounts.py`, `app/templates/whoami.html`, the user chip in `app/templates/base.html`,
`tests/test_whoami.py`). This document is self-contained: everything needed to build it in another
FastAPI + Jinja2 app is below.

---

## 1. Why every app with an admin allowlist wants this

An app that decides who is an administrator from a list in its own configuration (the
`admin_users` option) produces one support request over and over: **"I added my name in the
configuration but I don't see the admin buttons."** The cause is almost always one of three things,
and none of them can be seen from inside the app without a page like this:

1. the app was **not restarted** after the list changed (options are read when the process starts);
2. the string in the list is **not the string Home Assistant sends** (a friendly name instead of the
   login name, a typo, a different capital letter);
3. the person is not actually signed in as who they think (another HA user, a shared tablet).

The page turns a guessing game into one screen: here is exactly what arrived, here is whether it
matched, here is what to add.

What the person sees:

```
How the app sees you
+----------------------------------------------------------+
| User name (sent by Home Assistant)   jane.doe        |
| User id (sent by Home Assistant)     9f3c...e81a          |
| Administrator in this app            No                   |
| Names in the app's admin_users    1                    |
+----------------------------------------------------------+
Neither your user name nor your user id above matches any of the 1 name in the admin_users list.
Add jane.doe (or 9f3c...e81a) exactly as shown, save, and restart the app -- the list is only
read when the app starts. Upper and lower case do not matter.
```

---

## 2. What you must already have (prerequisites)

The page is a thin layer on top of the usual "identity from ingress headers + static admin
allowlist" setup. If the app already has that, skip to section 3. If not, this is the minimum.

### 2a. `config.yaml`: a real list-type option
```yaml
options:
  admin_users: []
schema:
  admin_users:
    - str
```

### 2b. Entrypoint (`bootstrap.py`): option -> environment variable, then start the server
Home Assistant writes the options to `/data/options.json` when the container starts. The app reads
`ADMIN_USERS` at import time, so it must be in the environment **before** the server process starts:

```python
import json, os
if os.path.exists("/data/options.json"):
    with open("/data/options.json") as f:
        options = json.load(f)
    os.environ["ADMIN_USERS"] = ",".join(options.get("admin_users", []))
os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8321"])
```
and the `Dockerfile` ends with `CMD ["python3", "bootstrap.py"]`.

### 2c. The headers Home Assistant's ingress proxy sends
| Header | Meaning |
|---|---|
| `X-Ingress-Path` | Set only by Supervisor's own ingress proxy. Its presence is what makes the other two headers trustworthy. |
| `X-Remote-User-Id` | The person's Home Assistant user id (a long hex string). |
| `X-Remote-User-Name` | The person's Home Assistant **login name**, for example `jane.doe`. Not necessarily the friendly name on their profile page. |

A request without `X-Ingress-Path` (or without a user id) must be refused (401): it did not come
through Home Assistant.

---

## 3. `auth.py`: match without regard to case, and expose one match function

```python
import os
from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request

ADMIN_USERS = {
    u.strip().strip("\"'").strip()
    for u in os.environ.get("ADMIN_USERS", "").split(",")
    if u.strip().strip("\"'").strip()
}
# Home Assistant user names are case-insensitive, and someone typing "Jane.Doe" into the
# configuration screen should not silently end up with no admin rights.
_ADMIN_FOLDED = {u.casefold() for u in ADMIN_USERS}


def is_admin_identity(user_id: str, name: str) -> bool:
    """True if the X-Remote-User-Id or X-Remote-User-Name is on the admin_users list."""
    return user_id.strip().casefold() in _ADMIN_FOLDED or name.strip().casefold() in _ADMIN_FOLDED


@dataclass
class User:
    id: str
    name: str
    is_admin: bool


def get_current_user(request: Request) -> User:
    ingress_path = request.headers.get("x-ingress-path")
    user_id = request.headers.get("x-remote-user-id")
    if not ingress_path or not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated via Home Assistant ingress")
    name = request.headers.get("x-remote-user-name", user_id)   # falls back to the id if the header is absent
    return User(id=user_id, name=name, is_admin=is_admin_identity(user_id, name))
```

Notes:
- Both the id and the name are compared, so whichever the person finds easier to copy works.
- `strip("\"'")` forgives quotes pasted into the list; `casefold()` forgives capitals.
- This must be the **real** identity. If the app has an "admin views as another user" feature, the
  whoami page must still use `get_current_user`, not the "acting user" dependency.

---

## 4. The route

```python
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from .auth import ADMIN_USERS, User, get_current_user

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/whoami")
def whoami(request: Request, current: User = Depends(get_current_user)):
    """The exact id and name Home Assistant sent, and whether either is on the admin_users list.
    Only the person's OWN identity and a COUNT are shown, never the list itself."""
    return templates.TemplateResponse(request, "whoami.html", {
        "user": current,                       # whatever base.html needs to draw the sidebar
        "is_admin": current.is_admin,
        "admin_entries": len(ADMIN_USERS),
        "name_sent": "x-remote-user-name" in request.headers,
    })
```

Register it with `app.include_router(router)`. Every link to it is **relative** (`href="whoami"`,
never `/whoami`), like every other link in an ingress app; a leading slash resolves against the
Home Assistant host instead of the app's ingress prefix and lands on Home Assistant's own frontend.

---

## 5. The template `whoami.html`

Adapt the wrapper (`extends`, blocks, card and table classes) to the app's own `base.html`; the
content is what matters:

```html
{% extends "base.html" %}
{% block title %}Who am I{% endblock %}
{% block content %}
<h1>How the app sees you</h1>
<div class="card">
  <table>
    <tr><th>User name (sent by Home Assistant)</th><td>{{ user.name if name_sent else "not sent" }}</td></tr>
    <tr><th>User id (sent by Home Assistant)</th><td>{{ user.id }}</td></tr>
    <tr><th>Administrator in this app</th><td><strong>{{ "Yes" if is_admin else "No" }}</strong></td></tr>
    <tr><th>Names in the app's admin_users list</th><td>{{ admin_entries }}</td></tr>
  </table>
</div>
{% if is_admin %}
<p class="hint">You are an administrator.</p>
{% else %}
<p class="hint">
  {% if admin_entries == 0 %}
  The admin_users list is <strong>empty</strong> in the running app. If you have filled it in, the app has not
  picked it up yet: changes to the app's options only take effect after it is <strong>restarted</strong>
  (Settings &rarr; Apps &rarr; this app &rarr; Restart).
  {% else %}
  Neither your user name nor your user id above matches any of the {{ admin_entries }}
  name{{ "s" if admin_entries != 1 else "" }} in the admin_users list. Add
  <strong>{{ user.name if name_sent else user.id }}</strong> (or <strong>{{ user.id }}</strong>) exactly as shown, save,
  and <strong>restart</strong> the app -- the list is only read when the app starts. Upper and lower case do not matter.
  {% endif %}
</p>
{% endif %}
{% endblock %}
```

Three cases, three messages: **admin** (Yes, nothing to do), **not admin and the list is empty**
(the running app has no entries: restart it), **not admin and the list has entries** (none match:
here is what to add).

---

## 6. Make it reachable: the user chip links to it

Wherever the sidebar or header shows the signed-in name, make it a link. Keep the "(admin)" marker,
which is itself a quick tell:

```html
<a href="whoami{{ qs }}" class="user-chip" title="How the app sees you">{{ user.name }}{% if user.is_admin %} (admin){% endif %}</a>
```

`qs` is whatever query-string carry-over the app already appends to internal links (for example
`?as_user=...`); drop it if there is none. A phone user in the Home Assistant companion app has no
address bar, so an in-page link is the only way they can reach the page at all.

---

## 7. Behaviours to match (acceptance list)

1. Signed in and on the list (by id **or** by name, any case): "Administrator: **Yes**", no advice.
2. Signed in and not on the list: "**No**", the exact name and id received, the entry count, and the
   "add ... and restart" advice. With an empty list the advice is about the restart instead.
3. The page never prints the admin list's contents, only the count.
4. It always reports the **real** signed-in person, even while an admin is viewing the app "as"
   someone else.
5. No `X-Ingress-Path` header means 401, like every other page.
6. Read-only: no forms, no writes, no side effects.
7. It echoes only the two identity headers (plus whether the name header was present). Do not dump the
   request's headers wholesale: proxies can add cookies or tokens that should never be on a screen.
8. The name in the sidebar links to it, and shows "(admin)" for administrators.

---

## 8. Tests worth copying

FastAPI `TestClient`, with `ADMIN_USERS=admin` set in the environment **before** the app is imported:

```python
from fastapi.testclient import TestClient

H = lambda uid, name: {"X-Remote-User-Id": uid, "X-Remote-User-Name": name, "X-Ingress-Path": "/x"}

r = client.get("whoami", headers=H("abc123", "jane.doe"))
assert "<strong>No</strong>" in r.text and "jane.doe" in r.text and "abc123" in r.text
assert "restart" in r.text.lower()
assert 'href="whoami' in r.text                                                     # the sidebar link

assert "<strong>Yes</strong>" in client.get("whoami", headers=H("admin", "Admin")).text    # exact
assert "<strong>Yes</strong>" in client.get("whoami", headers=H("ADMIN", "x")).text        # id, upper case
assert "<strong>Yes</strong>" in client.get("whoami", headers=H("zzz", "Admin")).text      # name, mixed case
assert "<strong>No</strong>"  in client.get("whoami", headers=H("zzz", "administrator")).text

# the list itself is never shown: add a second entry, load the page as a non-admin
import app.auth as auth
auth.ADMIN_USERS.add("hidden.person"); auth._ADMIN_FOLDED.add("hidden.person")
r = client.get("whoami", headers=H("abc123", "jane.doe"))
assert "hidden.person" not in r.text and "<td>2</td>" in r.text                     # count only

# fails closed without the ingress header
assert TestClient(app).get("whoami", headers={"X-Remote-User-Id": "admin"}).status_code == 401
```

Also check by hand once in the real app: open the page as an administrator and as a person not on
the list, and on a phone (no horizontal scrolling, the link in the sidebar is reachable).

---

## 9. Gotchas

- **The app must be restarted after `admin_users` changes.** Options reach the app as an environment
  variable read when the process starts (2b). Saving the Configuration tab does not restart the app.
  This is the number-one reason for "I added my name and nothing happened", and the reason the page says so.
- **`X-Remote-User-Name` is the Home Assistant login name** (like `jane.doe`), not necessarily the
  friendly name on the profile page. Do not guess: the page shows exactly what arrived, and that is the
  string to put in the list. The user id (long hex) works too.
- **Do not match on anything a person can edit themselves.** The list is compared only to the two
  identity headers Supervisor sets. If you ever add a "display name" to the match, first check the
  person cannot change it; otherwise a person could rename themselves into admin.
- **Keep the count, drop the list.** Showing the names would tell every user who the administrators
  are; the count is enough to tell "list is empty" from "list does not match".
- **Relative links only** (`whoami`, not `/whoami`).
- **This page is a diagnostic, not a security boundary.** Anything actually admin-only still needs
  its own `require_admin` dependency on the route behind it; hiding a link is not access control.

---

## 10. Optional extras (not built in Finance Dashboard)

- Show `X-Remote-User-Display-Name` if Supervisor sends it, labelled "display name (not used for admin)".
- Show "opened through Home Assistant ingress: yes" (the `X-Ingress-Path` header was present) as reassurance.
- A "copy" button next to the name for people on a desktop.

---

## 11. Build checklist

1. `config.yaml` has the `admin_users` list option (2a); the entrypoint turns it into `ADMIN_USERS` (2b).
2. `auth.py` has `is_admin_identity` and the fail-closed `get_current_user` (3).
3. Add the `/whoami` route (4) and register the router.
4. Add `whoami.html` (5), adapted to the app's own base template.
5. Make the sidebar name a relative link to it (6).
6. Copy the tests (8) and make them pass.
7. Bump the app's version in `config.yaml` so Supervisor offers the update, rebuild, then **restart**
   after any change to `admin_users`, and open the page once as an admin and once as a non-admin.
