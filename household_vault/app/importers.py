"""Importing from other password managers: CSV exports, and the
duplicate check used by every import.

CSV: the header row decides which column is which, so one reader covers the
common exports — Chrome / Edge ("name,url,username,password,note"), Firefox,
Bitwarden, LastPass, 1Password, Dashlane, Proton Pass and KeePassXC. Rows
without a password, username or website are skipped.

Duplicates: an item is a duplicate when an item with the same name,
username, website host and password is already in one of your open vaults
(or earlier in the same file). Importing the same export twice adds nothing.
"""
import csv
import io

from fastapi import HTTPException

from . import items, search

MAX_ROWS = 5000

# lower-cased header → our field (the first matching column wins)
COLUMNS = {
    "title": ("name", "title", "account", "item name", "entry", "login_name"),
    "username": ("username", "login_username", "login", "user", "user name", "email", "e-mail", "login name"),
    "password": ("password", "login_password", "pass"),
    "url": ("url", "login_uri", "website", "web site", "uri", "urls", "hostname", "login url"),
    "notes": ("note", "notes", "extra", "comments", "comment"),
    "totp": ("totp", "login_totp", "otpauth", "otp", "one-time password", "otp url", "2fa"),
    "folder": ("folder", "grouping", "group", "category", "vault", "collections"),
    "favourite": ("favorite", "favourite", "fav", "starred"),
}


def signature(title: str, username: str, url: str, password: str) -> tuple:
    return (search.fold(title).strip(), search.fold(username).strip(), items.host_of(url), password or "")


def signatures_of(d) -> set:
    out = set()
    for e in items.all_live_entries(d):
        f = d.get_fields(e)
        out.add(signature(f.get("Title", ""), f.get("UserName", ""), f.get("URL", ""), f.get("Password", "")))
    return out


def entry_signature(d, e) -> tuple:
    f = d.get_fields(e)
    return signature(f.get("Title", ""), f.get("UserName", ""), f.get("URL", ""), f.get("Password", ""))


def looks_like_csv(data: bytes) -> bool:
    head = data[:4096]
    return b"\0" not in head and b"," in head


def parse_csv(data: bytes) -> list:
    """[{title, username, password, url, notes, totp, folder, favourite}] from a CSV export."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except (StopIteration, csv.Error):
        raise HTTPException(422, "That CSV file is empty.")
    col = {}
    for key, names in COLUMNS.items():
        for n in names:
            if n in header:
                col[key] = header.index(n)
                break
    if "password" not in col or not ({"title", "url", "username"} & set(col)):
        raise HTTPException(422, "That CSV doesn't look like a password export (it needs a password column and a "
                                 "name, website or username column).")
    out = []
    try:
        for n, row in enumerate(reader):
            if n >= MAX_ROWS:
                raise HTTPException(422, f"That file has more than {MAX_ROWS} rows.")
            get = lambda k: (row[col[k]] if k in col and col[k] < len(row) else "").strip()
            rec = {k: get(k) for k in COLUMNS}
            if not (rec["password"] or rec["username"] or rec["url"]):
                continue
            if not rec["title"]:
                rec["title"] = items.host_of(rec["url"]) or rec["username"] or "Imported"
            rec["favourite"] = rec["favourite"].lower() in ("1", "true", "yes", "y", "x", "*")
            out.append(rec)
    except csv.Error as e:
        raise HTTPException(422, f"The CSV file couldn't be read: {e}")
    return out


def add_records(d, group, records: list, seen: set | None) -> tuple[int, int]:
    """Add CSV records under `group` (their folder column becomes subfolders). Returns (added, skipped)."""
    from . import totp
    added = skipped = 0
    folders: dict = {}
    for rec in records:
        sig = signature(rec["title"], rec["username"], rec["url"], rec["password"])
        if seen is not None:
            if sig in seen:
                skipped += 1
                continue
            seen.add(sig)
        target = group
        if rec["folder"]:
            path = [p.strip() for p in rec["folder"].replace("\\", "/").split("/") if p.strip()][:5]
            key = tuple(path)
            if key not in folders:
                g = group
                for name in path:
                    found = next((x for x in g if x.tag == "Group" and x.findtext("Name") == name[:100]), None)
                    g = found if found is not None else d.add_group(g, name[:100])
                folders[key] = g
            target = folders[key]
        body = {"type": "login", "title": rec["title"][:500], "username": rec["username"][:500], "password": rec["password"][:1000],
                "url": rec["url"][:500], "notes": rec["notes"][:items.MAX_FIELD], "favourite": rec["favourite"]}
        if rec["totp"]:
            try:
                totp.parse(rec["totp"])
                body["totp"] = rec["totp"]
            except totp.BadOtp:
                body["notes"] = (body["notes"] + "\n\n2FA (couldn't read): " + rec["totp"]).strip()
        items.create(d, target, body)
        added += 1
    return added, skipped
