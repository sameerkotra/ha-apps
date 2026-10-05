# Shared file: edit common/python/whoami.py and run tools/sync_common.py; don't edit this copy. sha256=9eb672214c2152accca8e02edb5d50bc6ff8288d2705520362c68ed8c69463c1
""""How the app sees you": the shared part of every app's whoami data (shared: common/python/whoami.py).

Each app keeps its own route (`GET /api/whoami`, Receipt's `/api/v1/whoami`, Finance's server-rendered
`/whoami` page) and its own idea of who the caller is; its route calls `build()` with the values it
already has, plus `extras`: the app's own rows for the page. The page (common/static/whoami.js, or
Finance's template) renders the result. Contract and rules: WHOAMI_PAGE_SPEC.md.

    from .common import whoami
    return whoami.build(request, user_id=u["id"], username=u["username"], display_name=u["name"],
                        is_admin=u["is_admin"], admin_entries=len(config.ADMIN_NAMES),
                        notify_linked=linked,
                        extras=[whoami.row("Chats you're in", n), whoami.notify_row(linked)])

Only the caller's OWN identity values, a COUNT of admin_users entries and the app's counts and
statuses go out: never the admin list, never other people's names, never secrets, never the
request's headers wholesale. Needs nothing from the app (no `from .. import`).
"""

# What a row's value may be: shown as text, a bool as Yes/No, None as "—".
_SCALARS = (str, int, float, bool, type(None))
_TONES = (None, "danger")

# Field names build() writes itself; an app's own fields must not reuse them.
FIELDS = ("haUserId", "haUsername", "haDisplayName", "nameSent", "isAdmin", "displayNameOnly",
          "adminEntries", "noAdmin", "viaIngress", "notifyLinked", "extras")

NOTIFY_LABEL = "Phone linked for notifications"
NOTIFY_HINT = ("No phone is linked to you yet. In Home Assistant: Settings → People → you → Track device "
               "(your phone with the Companion app). It's picked up within 5 minutes.")


def row(label: str, value, hint: str | None = None, *, tone: str | None = None,
        action: dict | None = None) -> dict:
    """One extra row for the page: {label, value[, hint][, tone][, action]}.

    value: text or a number (shown as is), a bool (Yes / No) or None ("—"). Counts and statuses only.
    hint: one sentence shown under the table (how to change this row's answer).
    tone: "danger" draws the value in the danger colour.
    action: {"label": button text, "target": a name the app's page knows} — a link button in the value
    cell (the page passes `target` to its onAction hook); with value None the button is the value."""
    if not isinstance(label, str) or not label:
        raise ValueError("a whoami row needs a label")
    if not isinstance(value, _SCALARS):
        raise ValueError(f"whoami row {label!r}: the value must be text, a number, a bool or None")
    if tone not in _TONES:
        raise ValueError(f"whoami row {label!r}: unknown tone {tone!r}")
    out = {"label": label, "value": value}
    if hint:
        out["hint"] = str(hint)
    if tone:
        out["tone"] = tone
    if action:
        if not (isinstance(action, dict) and action.get("label") and action.get("target")):
            raise ValueError(f"whoami row {label!r}: an action needs a label and a target")
        out["action"] = {"label": str(action["label"]), "target": str(action["target"])}
    return out


def notify_row(linked: bool, *, label: str = NOTIFY_LABEL, hint: str | None = NOTIFY_HINT) -> dict:
    """The usual "is a phone linked" row: Yes / No, with `hint` (how to link one) only when it isn't."""
    return row(label, bool(linked), None if linked else hint)


def build(request, *, user_id: str, username: str | None, display_name: str | None, is_admin: bool,
          admin_entries: int, display_name_only: bool = False, notify_linked: bool | None = None,
          extras=(), **app_fields) -> dict:
    """The whoami JSON. `request`: the caller's request (only "is X-Ingress-Path there" is read).
    user_id / username / display_name: what Home Assistant sent (username None when the login-name
    header was absent). admin_entries: len(admin_users). display_name_only: the caller isn't an admin
    but their display name is on the list (display names never count). notify_linked: whether a phone
    or notify service reaches them (None: the app has no notifications; the field is then left out).
    extras: row()s. app_fields: the app's own extra top-level fields for its own code."""
    clash = sorted(set(app_fields) & set(FIELDS))
    if clash:
        raise ValueError(f"app fields reuse shared whoami names: {clash}")
    rows = []
    for r in extras or ():
        if r is None:
            continue
        if not (isinstance(r, dict) and "label" in r and "value" in r):
            raise ValueError("whoami extras must be row() dicts")
        rows.append(r)
    admin_entries = int(admin_entries)
    out = {
        "haUserId": user_id,
        "haUsername": username or None,
        "haDisplayName": display_name or None,
        "nameSent": bool(username),
        "isAdmin": bool(is_admin),
        "displayNameOnly": bool(display_name_only) and not is_admin,
        "adminEntries": admin_entries,
        "noAdmin": admin_entries == 0,          # first run: nobody can be an admin yet ("No admin yet" banner)
        "viaIngress": request is not None and "x-ingress-path" in request.headers,
    }
    if notify_linked is not None:
        out["notifyLinked"] = bool(notify_linked)
    out["extras"] = rows
    out.update(app_fields)
    return out
