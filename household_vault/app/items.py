"""Items and folders inside an open vault (SPEC §5.5, §9.3).

API ids are the KeePass UUIDs as 32 hex characters. Protected fields
(passwords, TOTP secrets, card numbers, CVVs, PINs, any field KeePass marks
protected) are **never** included in summaries or details — only a
`{"set": true}` marker; `secret()` returns one field on demand.
"""
import base64
import binascii
import datetime
import re
import urllib.parse

from fastapi import HTTPException

from . import kdbx, totp

TYPE_KEY = "HouseholdVault.Type"
PREV_GROUP_KEY = "HouseholdVault.PreviousGroup"
TYPES = ("login", "note", "card")
CARD_FIELDS = ("Cardholder", "Number", "Expiry", "CVV", "PIN")
ALWAYS_PROTECTED = {"Password", "otp", "TOTP Seed", "Number", "CVV", "PIN", totp.KEEPASS_SECRET}
# every field a TOTP can live in: KeePassXC's `otp` (ours), its old pair, and KeePass's own (written too)
OTP_FIELDS = {"otp", "TOTP Seed", "TOTP Settings", *totp.KEEPASS_FIELDS, *totp.KEEPASS_OTHER_SECRETS}
FAVOURITE = "favourite"
SHEET_KEY = "HouseholdVault.Sheet"              # "1" = on the printable household sheet (SPEC §12.3)
SHEET_FIELDS_KEY = "HouseholdVault.SheetFields" # JSON list of the fields to print
SHEET_WIFI_KEY = "HouseholdVault.SheetWifi"     # WPA / WEP / nopass → print a Wi-Fi QR code
NEVER_PRINTED = {"Number", "CVV", "PIN", "Title"} | OTP_FIELDS
WIFI_SECURITY = ("WPA", "WEP", "nopass")
TEMPLATE_KEY = "HouseholdVault.Template"       # which form the item was made from
REMIND_KEY = "HouseholdVault.RemindDays"       # days before the expiry date to remind (0 = don't)
TEMPLATES = ("wifi", "bank", "insurance", "vehicle", "identity", "licence", "software", "utility", "membership")
REMIND_CHOICES = (0, 1, 7, 14, 30, 60, 90)
DEFAULT_REMIND = 30
MAX_FIELD = 20000
MAX_ENTRIES = 5000


def to_id(b64: str) -> str:
    return base64.b64decode(b64).hex()


def to_b64(hex_id: str) -> str:
    try:
        raw = binascii.unhexlify(hex_id)
    except (binascii.Error, ValueError):
        raise HTTPException(404, "Not found.")
    if len(raw) != 16:
        raise HTTPException(404, "Not found.")
    return base64.b64encode(raw).decode()


def host_of(url: str) -> str:
    if not url:
        return ""
    u = url if "://" in url else "https://" + url
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def entry_type(d: kdbx.Database, e) -> str:
    t = d.custom_data(e).get(TYPE_KEY)
    if t in TYPES:
        return t
    tags = {x.lower() for x in d.tags(e)}
    if "card" in tags or d.get_field(e, "Number") is not None and d.get_field(e, "CVV") is not None:
        return "card"
    if "note" in tags:
        return "note"
    return "login"


def expires_on(d: kdbx.Database, e):
    """The KeePass expiry date (Times/Expires + ExpiryTime) as a date, or None."""
    t = e.find("Times")
    if t is None or (t.findtext("Expires") or "").lower() != "true":
        return None
    when = kdbx.parse_time(t.findtext("ExpiryTime"))
    return when.date() if when else None


def set_expires(d: kdbx.Database, e, when) -> None:
    t = e.find("Times")
    if t is None:
        return
    for tag in ("ExpiryTime", "Expires"):
        if t.find(tag) is None:
            kdbx._sub(t, tag, "")
    if when is None:
        t.find("Expires").text = "False"
        return
    t.find("ExpiryTime").text = kdbx.fmt_time(datetime.datetime(when.year, when.month, when.day, tzinfo=datetime.timezone.utc))
    t.find("Expires").text = "True"


def remind_days(d: kdbx.Database, e) -> int:
    try:
        v = int(d.custom_data(e).get(REMIND_KEY, DEFAULT_REMIND))
    except ValueError:
        v = DEFAULT_REMIND
    return v if v in REMIND_CHOICES else DEFAULT_REMIND


def template_of(d: kdbx.Database, e) -> str | None:
    t = d.custom_data(e).get(TEMPLATE_KEY)
    return t if t in TEMPLATES else None


def card_expiry_date(text: str):
    """"MM/YY" or "MM/YYYY" on a card → the last day of that month."""
    m = re.match(r"^\s*(\d{1,2})\s*[/\-.]\s*(\d{2}|\d{4})\s*$", text or "")
    if not m:
        return None
    month, year = int(m.group(1)), int(m.group(2))
    if year < 100:
        year += 2000
    if not 1 <= month <= 12:
        return None
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return nxt - datetime.timedelta(days=1)


def otp_params(d: kdbx.Database, e):
    raw = d.get_field(e, "otp")
    try:
        if raw:
            return totp.parse(raw)
        seed = d.get_field(e, "TOTP Seed")
        if seed:
            return totp.from_legacy(seed, d.get_field(e, "TOTP Settings"))
        return totp.from_keepass(d.get_fields(e))
    except totp.BadOtp:
        return None


def sync_keepass_otp(d: kdbx.Database) -> int:
    """Give every item with a TOTP KeePass's own fields too (`TimeOtp-Secret-Base32` …), so KeePass 2.47+
    shows the code as well as KeePassXC/KeePassDX/Strongbox (which read `otp`). No history entry is made:
    the code itself doesn't change. → how many items were updated."""
    n = 0
    for e in list(d.entries.values()):
        f = d.get_fields(e)
        if not (f.get("otp") or f.get("TOTP Seed")):
            continue
        p = otp_params(d, e)
        if p is None:
            continue
        want = totp.keepass_fields(p)
        stale = [k for k in totp.KEEPASS_FIELDS if k in f and k not in want]
        if all(f.get(k) == v for k, v in want.items()) and not stale:
            continue
        for k, v in want.items():
            d._set_string(e, k, v, k == totp.KEEPASS_SECRET)
        for k in stale:
            d.remove_field(e, k)
        n += 1
    return n


def find_entry(d: kdbx.Database, item_id: str):
    e = d.entries.get(to_b64(item_id))
    if e is None or d.parent.get(e) is None:
        raise HTTPException(404, "That item doesn't exist (it may have been deleted).")
    return e


def find_group(d: kdbx.Database, folder_id: str | None):
    if not folder_id:
        return d.root_group
    g = d.groups.get(to_b64(folder_id))
    if g is None:
        raise HTTPException(404, "That folder doesn't exist.")
    return g


def summary(d: kdbx.Database, e, vault: dict) -> dict:
    f = d.get_fields(e)
    tags = d.tags(e)
    t = entry_type(d, e)
    g = d.parent.get(e)
    number = f.get("Number") or ""
    return {"id": to_id(e.findtext("UUID")), "vaultId": vault["id"], "vaultName": vault["name"], "type": t,
            "title": f.get("Title", ""), "username": f.get("UserName", "") if t == "login" else f.get("Cardholder", ""),
            "url": f.get("URL", ""), "host": host_of(f.get("URL", "")),
            "tags": [x for x in tags if x.lower() not in (FAVOURITE, "card", "note")],
            "favourite": any(x.lower() == FAVOURITE for x in tags),
            "folderId": to_id(g.findtext("UUID")) if g is not None and g is not d.root_group else None,
            "folderPath": d.group_path(g) if g is not None else [],
            "hasTotp": otp_params(d, e) is not None,
            "cardLast4": number[-4:] if t == "card" and len(number) >= 4 else None,
            "template": template_of(d, e), "expires": _iso(expires_on(d, e)),
            "inTrash": d.in_recycle_bin(e), "modified": d.modified(e)}


def _iso(day) -> str | None:
    return day.isoformat() if day else None


def detail(d: kdbx.Database, e, vault: dict) -> dict:
    out = summary(d, e, vault)
    f = d.get_fields(e)
    prot = d.protected_keys(e) | (ALWAYS_PROTECTED & set(f))
    known = set(kdbx.STANDARD_FIELDS) | set(CARD_FIELDS) | OTP_FIELDS
    out["notes"] = f.get("Notes", "")
    out["username"] = f.get("UserName", "")
    out["password"] = {"set": bool(f.get("Password"))}
    out["cardholder"] = f.get("Cardholder", "")
    out["expiry"] = f.get("Expiry", "")
    out["card"] = {k.lower(): {"set": bool(f.get(k))} for k in ("Number", "CVV", "PIN")}
    out["fields"] = [{"name": k, "protected": k in prot, "value": None if k in prot else v}
                     for k, v in f.items() if k not in known]
    out["historyCount"] = len(d.history(e))
    out["created"] = e.findtext("Times/CreationTime") or ""
    p = otp_params(d, e)
    out["totp"] = {"digits": p["digits"], "period": p["period"], "issuer": p["issuer"]} if p else None
    out["emergency"] = d.custom_data(e).get("HouseholdVault.Emergency") == "1"
    out["remindDays"] = remind_days(d, e)
    out["sheet"] = sheet_settings(d, e)
    out["sheetFields"] = printable_fields(d, e)
    return out


def printable_fields(d: kdbx.Database, e) -> list:
    """Fields that may go on the household sheet — never card numbers, CVVs, PINs or 2FA secrets."""
    return [k for k, v in d.get_fields(e).items() if k not in NEVER_PRINTED and v]


def sheet_settings(d: kdbx.Database, e) -> dict:
    import json
    cd = d.custom_data(e)
    try:
        fields = [f for f in json.loads(cd.get(SHEET_FIELDS_KEY) or "[]") if isinstance(f, str)]
    except ValueError:
        fields = []
    wifi = cd.get(SHEET_WIFI_KEY)
    return {"include": cd.get(SHEET_KEY) == "1", "fields": fields, "wifi": wifi if wifi in WIFI_SECURITY else None}


def set_sheet(d: kdbx.Database, e, include: bool, fields: list, wifi: str | None) -> None:
    import json
    allowed = set(printable_fields(d, e))
    bad = [f for f in fields if f not in allowed]
    if bad:
        raise HTTPException(422, f"“{bad[0]}” can't go on the sheet.")
    if wifi is not None and wifi not in WIFI_SECURITY:
        raise HTTPException(422, "Wi-Fi security must be WPA, WEP or nopass.")
    if wifi and wifi != "nopass" and not d.get_field(e, "Password"):
        raise HTTPException(422, "A Wi-Fi QR code needs the network's password in the Password field.")
    d.set_custom_data(e, SHEET_KEY, "1" if include else None)
    d.set_custom_data(e, SHEET_FIELDS_KEY, json.dumps(fields) if include else None)
    d.set_custom_data(e, SHEET_WIFI_KEY, wifi if include and wifi else None)


def sheet_entry(d: kdbx.Database, e) -> dict | None:
    """What gets printed for one entry (includes secrets — only for the print page)."""
    st = sheet_settings(d, e)
    if not st["include"]:
        return None
    f = d.get_fields(e)
    allowed = set(printable_fields(d, e))
    out = {"title": f.get("Title", ""), "fields": [{"name": k, "value": f[k]} for k in st["fields"] if k in allowed], "wifi": None}
    if st["wifi"]:
        out["wifi"] = {"ssid": f.get("UserName") or f.get("Title", ""), "password": f.get("Password", "") if st["wifi"] != "nopass" else "",
                       "security": st["wifi"]}
    return out


LOCAL_MARKS = ("HouseholdVault.Emergency", SHEET_KEY, SHEET_FIELDS_KEY, SHEET_WIFI_KEY)


def strip_marks(d: kdbx.Database, e) -> None:
    """A copy is a new item: it isn't in anyone's emergency access or on a household sheet until someone says so."""
    for k in LOCAL_MARKS:
        d.set_custom_data(e, k, None)


def secret(d: kdbx.Database, e, field: str) -> str:
    if field == "totpUri":
        p = otp_params(d, e)
        if p is None:
            raise HTTPException(404, "No 2FA code on this item.")
        return totp.to_uri(p, d.get_field(e, "Title") or "account")
    v = d.get_field(e, field)
    if v is None:
        raise HTTPException(404, "No such field.")
    return v


def history(d: kdbx.Database, e) -> list:
    out = []
    for h in reversed(d.history(e)):
        f = d.get_fields(h)
        out.append({"modified": h.findtext("Times/LastModificationTime") or "", "title": f.get("Title", ""),
                    "username": f.get("UserName", ""), "url": f.get("URL", ""),
                    "passwordChanged": None})
    # mark where the password differs from the next newer version (without revealing it)
    newer = d.get_field(e, "Password")
    for item, h in zip(out, reversed(d.history(e))):
        pw = d.get_field(h, "Password")
        item["passwordChanged"] = pw != newer
        newer = pw
    return out


# ---------- writing ----------

def _clean(v, limit=MAX_FIELD) -> str:
    s = "" if v is None else str(v)
    if len(s) > limit:
        raise HTTPException(422, "A field is too long.")
    return s


def apply(d: kdbx.Database, e, body: dict, creating: bool) -> None:
    """Set an item's fields from the API body (only keys present are changed)."""
    t = body.get("type") or (entry_type(d, e) if e is not None else "login")
    if t not in TYPES:
        raise HTTPException(422, "Type must be login, note or card.")
    fields, protected, remove = {}, set(), set()
    if "title" in body:
        title = _clean(body["title"], 500).strip()
        if not title:
            raise HTTPException(422, "Give it a name.")
        fields["Title"] = title
    elif creating:
        raise HTTPException(422, "Give it a name.")
    for key, api in (("UserName", "username"), ("URL", "url"), ("Notes", "notes"), ("Cardholder", "cardholder"),
                     ("Expiry", "expiry")):
        if api in body:
            fields[key] = _clean(body[api], 500 if key != "Notes" else MAX_FIELD)
    if "password" in body and body["password"] is not None:
        fields["Password"] = _clean(body["password"], 1000)
        protected.add("Password")
    for k in ("Number", "CVV", "PIN"):
        api = k.lower()
        if body.get("card") and api in body["card"] and body["card"][api] is not None:
            fields[k] = _clean(body["card"][api], 100).replace(" ", "") if k == "Number" else _clean(body["card"][api], 100)
            protected.add(k)
    if "totp" in body:
        raw = (body["totp"] or "").strip()
        if raw:
            try:
                p = totp.parse(raw)
            except totp.BadOtp as x:
                raise HTTPException(422, str(x))
            fields["otp"] = raw if raw.lower().startswith("otpauth://") else totp.to_uri(p, fields.get("Title") or "account")
            protected.add("otp")
            kp = totp.keepass_fields(p)                      # KeePass 2.47+ reads its own fields, not `otp`
            fields.update(kp)
            protected.add(totp.KEEPASS_SECRET)
            if e is not None:
                remove |= (OTP_FIELDS - {"otp"}) - set(kp)
        elif e is not None:
            remove |= OTP_FIELDS
    if "fields" in body and body["fields"] is not None:
        names = set()
        for f in body["fields"][:50]:
            name = _clean(f.get("name"), 100).strip()
            if not name or name in kdbx.STANDARD_FIELDS or name in CARD_FIELDS or name in OTP_FIELDS:
                raise HTTPException(422, f"“{name}” can't be used as a field name.")
            if name in names:
                raise HTTPException(422, f"Two fields are called “{name}”.")
            names.add(name)
            if f.get("value") is None and e is not None:        # protected, unchanged
                continue
            fields[name] = _clean(f.get("value"))
            if f.get("protected"):
                protected.add(name)
        if e is not None:
            known = set(kdbx.STANDARD_FIELDS) | set(CARD_FIELDS) | OTP_FIELDS
            remove |= {k for k in d.get_fields(e) if k not in known and k not in names}
    if e is None:
        return fields, protected, t
    if fields or remove:
        d.update_entry(e, fields, protected, remove)
    d.set_custom_data(e, TYPE_KEY, t)
    _apply_tags(d, e, body, t)
    _apply_meta(d, e, body, t)
    return None


def _apply_meta(d, e, body, t):
    """Expiry date, reminder and template."""
    if "template" in body:
        tpl = body["template"] or None
        if tpl is not None and tpl not in TEMPLATES:
            raise HTTPException(422, "Unknown template.")
        d.set_custom_data(e, TEMPLATE_KEY, tpl)
    if "expires" in body:
        raw = (body["expires"] or "").strip()
        if raw:
            try:
                day = datetime.date.fromisoformat(raw)
            except ValueError:
                raise HTTPException(422, "The expiry date must look like 2027-03-31.")
            if not 1900 <= day.year <= 2200:
                raise HTTPException(422, "That expiry date is out of range.")
            set_expires(d, e, day)
        else:
            set_expires(d, e, None)
    elif t == "card" and "expiry" in body and expires_on(d, e) is None:
        day = card_expiry_date(body["expiry"])
        if day:
            set_expires(d, e, day)
    if "remindDays" in body and body["remindDays"] is not None:
        if body["remindDays"] not in REMIND_CHOICES:
            raise HTTPException(422, "Remind 1, 7, 14, 30, 60 or 90 days before, or not at all.")
        d.set_custom_data(e, REMIND_KEY, None if body["remindDays"] == DEFAULT_REMIND else str(body["remindDays"]))


def _apply_tags(d, e, body, t):
    tags = d.tags(e)
    fav = any(x.lower() == FAVOURITE for x in tags)
    if "tags" in body and body["tags"] is not None:
        tags = [_clean(x, 60).strip() for x in body["tags"][:30]]
    else:
        tags = [x for x in tags if x.lower() not in (FAVOURITE, "card", "note")]
    if "favourite" in body and body["favourite"] is not None:
        fav = bool(body["favourite"])
    extra = [t] if t in ("card", "note") else []
    d.set_tags(e, [x for x in tags if x.lower() not in (FAVOURITE, "card", "note")] + extra + ([FAVOURITE] if fav else []))


def create(d: kdbx.Database, group, body: dict):
    if len(d.entries) >= MAX_ENTRIES:
        raise HTTPException(409, "This vault has 5,000 items — the most it can hold.")
    fields, protected, t = apply(d, None, body, creating=True)
    e = d.add_entry(group, fields, protected)
    d.set_custom_data(e, TYPE_KEY, t)
    _apply_tags(d, e, body, t)
    _apply_meta(d, e, body, t)
    return e


def trash(d: kdbx.Database, el) -> str:
    """Move to the Recycle Bin, or delete for good if it's already there. Returns 'trashed'/'deleted'."""
    if d.in_recycle_bin(el):
        d.delete_permanently(el)
        return "deleted"
    rb = d.recycle_bin(create=True)
    if rb is None:                      # recycle bin turned off in this file
        d.delete_permanently(el)
        return "deleted"
    prev = d.parent.get(el)
    d.set_custom_data(el, PREV_GROUP_KEY, prev.findtext("UUID") if prev is not None else None)
    d.move(el, rb)
    return "trashed"


def restore(d: kdbx.Database, el) -> None:
    prev = d.custom_data(el).get(PREV_GROUP_KEY)
    target = d.groups.get(prev) if prev else None
    if target is None or d.in_recycle_bin(target):
        target = d.root_group
    d.set_custom_data(el, PREV_GROUP_KEY, None)
    d.move(el, target)


def folders(d: kdbx.Database) -> list:
    """The folder tree (without the Recycle Bin): [{id, name, parentId, count, depth}] in tree order."""
    rb = d.recycle_bin()
    out = []

    def walk(g, depth, parent_id):
        for child in g:
            if child.tag != "Group" or child is rb:
                continue
            cid = to_id(child.findtext("UUID"))
            count = sum(1 for x in child if x.tag == "Entry")
            out.append({"id": cid, "name": child.findtext("Name") or "", "parentId": parent_id, "count": count,
                        "depth": depth})
            walk(child, depth + 1, cid)
    walk(d.root_group, 0, None)
    return out


def trash_contents(d: kdbx.Database, vault: dict) -> dict:
    rb = d.recycle_bin()
    if rb is None:
        return {"items": [], "folders": []}
    items = [summary(d, e, vault) for e in entries_in(d, rb, True)]
    fol = [{"id": to_id(g.findtext("UUID")), "name": g.findtext("Name") or ""} for g in rb if g.tag == "Group"]
    return {"items": items, "folders": fol}


def entries_in(d: kdbx.Database, group, recursive: bool) -> list:
    rb = d.recycle_bin()
    out = []
    stack = [group]
    while stack:
        g = stack.pop()
        for child in g:
            if child.tag == "Entry":
                out.append(child)
            elif child.tag == "Group" and recursive and child is not rb:
                stack.append(child)
    return out


def all_live_entries(d: kdbx.Database) -> list:
    return entries_in(d, d.root_group, True)
