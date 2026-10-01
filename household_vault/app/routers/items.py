"""Folders, items and search inside open vaults (SPEC §8, §9.3, §9.4)."""
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from .. import config, db, items, search, service, sessions, totp
from .common import Ctx, Strict, unlocked, vault_access
from .me import _vault_list

router = APIRouter(prefix="/api", tags=["items"])

# "Recently used" — per person, kept across sessions: vault and item ids only, never titles
RECENT_KEEP = 30
_secret_hits: dict = {}


def _touch_recent(ctx: Ctx, vid: str, iid: str) -> None:
    with db.get_conn() as conn:
        conn.execute("INSERT INTO recent_items (user_id, vault_id, item_id, used_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(user_id, vault_id, item_id) DO UPDATE SET used_at = excluded.used_at",
                     (ctx.user["id"], vid, iid, config.now_iso()))
        conn.execute("DELETE FROM recent_items WHERE user_id = ? AND rowid NOT IN (SELECT rowid FROM recent_items "
                     "WHERE user_id = ? ORDER BY used_at DESC LIMIT ?)", (ctx.user["id"], ctx.user["id"], RECENT_KEEP))


def _recent_keys(ctx: Ctx) -> list:
    with db.get_conn() as conn:
        return [(r["vault_id"], r["item_id"]) for r in conn.execute(
            "SELECT vault_id, item_id FROM recent_items WHERE user_id = ? ORDER BY used_at DESC", (ctx.user["id"],))]


def _rate_limit_secret(ctx: Ctx) -> None:
    """At most 60 secrets a minute per session (copy / reveal is a person, not a script)."""
    now = time.time()
    hits = [t for t in _secret_hits.get(ctx.session.token, []) if now - t < 60]
    if len(hits) >= 60:
        raise HTTPException(429, "Slow down — too many secrets revealed in a minute.")
    hits.append(now)
    _secret_hits[ctx.session.token] = hits


def _vault_dict(v: dict) -> dict:
    return {"id": v["id"], "name": v["name"] if v["kind"] != "personal" else "Personal"}


def _vault_label(conn, ctx, v: dict) -> dict:
    if v["kind"] in ("personal", "emergency") and v["owner_user_id"] != ctx.user["id"]:
        o = conn.execute("SELECT name FROM users WHERE id = ?", (v["owner_user_id"],)).fetchone()
        who = o["name"] if o else "Someone"
        return {"id": v["id"], "name": f"{who}'s Personal" if v["kind"] == "personal" else f"{who}'s emergency items"}
    return _vault_dict(v)


# ---------- folders ----------
@router.get("/vaults/{vid}/folders")
def get_folders(vid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
    with ov.lock:
        d = ov.db
        rb = d.recycle_bin()
        root_count = sum(1 for x in d.root_group if x.tag == "Entry")
        trash_count = len(items.entries_in(d, rb, True)) if rb is not None else 0
        return {"rootId": items.to_id(d.root_group.findtext("UUID")), "rootCount": root_count,
                "folders": items.folders(d), "trashCount": trash_count, "canEdit": role in service.EDIT_ROLES}


class FolderIn(Strict):
    name: str = Field(min_length=1, max_length=100)
    parentId: str | None = None


def _folder_name(n: str) -> str:
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        raise HTTPException(422, "Give the folder a name.")
    return n


@router.post("/vaults/{vid}/folders", status_code=201)
def create_folder(vid: str, body: FolderIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            parent = items.find_group(ov.db, body.parentId)
            if ov.db.in_recycle_bin(parent):
                raise HTTPException(409, "You can't make folders in the Trash.")
            g = ov.db.add_group(parent, _folder_name(body.name))
            service.save(conn, ov, ctx.user["id"])
            return {"id": items.to_id(g.findtext("UUID")), "folders": items.folders(ov.db)}


class FolderPatch(Strict):
    name: str | None = Field(default=None, max_length=100)
    parentId: str | None = None
    toRoot: bool = False


def _own_folder(d, fid: str, verb: str):
    """A folder people made (not the top level or the Trash)."""
    g = items.find_group(d, fid)
    if g is d.root_group or g is d.recycle_bin():
        raise HTTPException(409, f"That folder can't be {verb}.")
    return g


@router.patch("/vaults/{vid}/folders/{fid}")
def edit_folder(vid: str, fid: str, body: FolderPatch, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            d = ov.db
            g = _own_folder(d, fid, "changed")
            if body.name is not None:
                g.find("Name").text = _folder_name(body.name)
                d.touch(g)
            if body.parentId or body.toRoot:
                target = d.root_group if body.toRoot else items.find_group(d, body.parentId)
                if d.is_descendant(target, g):
                    raise HTTPException(422, "A folder can't go inside itself.")
                if d.in_recycle_bin(target):
                    raise HTTPException(409, "Use Delete to move a folder to the Trash.")
                d.move(g, target)
            service.save(conn, ov, ctx.user["id"])
            return {"folders": items.folders(d)}


@router.delete("/vaults/{vid}/folders/{fid}")
def delete_folder(vid: str, fid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            d = ov.db
            g = _own_folder(d, fid, "deleted")
            empty = not any(x.tag in ("Entry", "Group") for x in g)
            if empty:
                d.delete_permanently(g)
                result = "deleted"
            else:
                result = items.trash(d, g)
            service.save(conn, ov, ctx.user["id"])
            return {"result": result, "folders": items.folders(d)}


# ---------- items ----------
@router.get("/vaults/{vid}/items")
def list_items(vid: str, folder: str | None = None, recursive: bool = False, trash: bool = False,
               ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
        label = _vault_label(conn, ctx, v)
    with ov.lock:
        d = ov.db
        if trash:
            return items.trash_contents(d, label)
        g = items.find_group(d, folder)
        es = items.entries_in(d, g, recursive)
        out = [items.summary(d, e, label) for e in es]
    out.sort(key=lambda x: search.fold(x["title"]))
    return {"items": out}


@router.get("/tags")
def tags(ctx: Ctx = Depends(unlocked)):
    """Every tag in the open vaults, with how many items have it."""
    with db.get_conn() as conn:
        vaults = [v for v in _vault_list(conn, ctx.user["id"], ctx.session) if v["open"]]
    counts: dict = {}
    names: dict = {}
    for vr in vaults:
        ov = sessions.get_open(vr["id"])
        if ov is None:
            continue
        with ov.lock:
            for e in items.all_live_entries(ov.db):
                for t in ov.db.tags(e):
                    k = search.fold(t)
                    if k in (items.FAVOURITE, "card", "note"):
                        continue
                    counts[k] = counts.get(k, 0) + 1
                    names.setdefault(k, t)
    return {"tags": sorted(({"tag": names[k], "count": n} for k, n in counts.items()), key=lambda x: search.fold(x["tag"]))}


@router.get("/items")
def all_items(favourites: bool = False, recent: bool = False, type: str | None = None,
              tag: str | None = Query(default=None, max_length=60), ctx: Ctx = Depends(unlocked)):
    """Everything in every open vault — or favourites, recently used, one type, or one tag."""
    with db.get_conn() as conn:
        vaults = [v for v in _vault_list(conn, ctx.user["id"], ctx.session) if v["open"]]
    out = []
    for vr in vaults:
        ov = sessions.get_open(vr["id"])
        if ov is None:
            continue
        with ov.lock:
            for e in items.all_live_entries(ov.db):
                s = items.summary(ov.db, e, {"id": vr["id"], "name": vr["name"]})
                if favourites and not s["favourite"]:
                    continue
                if type and s["type"] != type:
                    continue
                if tag and search.fold(tag) not in {search.fold(t) for t in s["tags"]}:
                    continue
                out.append(s)
    if recent:
        keys = _recent_keys(ctx)
        pos = {k: i for i, k in enumerate(keys)}
        out = sorted([s for s in out if (s["vaultId"], s["id"]) in pos], key=lambda s: pos[(s["vaultId"], s["id"])])
    else:
        out.sort(key=lambda x: search.fold(x["title"]))
    return {"items": out}


@router.get("/expiring")
def expiring(within: int = Query(default=90, ge=1, le=3650), ctx: Ctx = Depends(unlocked)):
    """Items with an expiry date in the open vaults: expired, or expiring within `within` days (soonest first)."""
    import datetime
    with db.get_conn() as conn:
        vaults = [v for v in _vault_list(conn, ctx.user["id"], ctx.session) if v["open"]]
    today = datetime.date.today()
    out = []
    for vr in vaults:
        ov = sessions.get_open(vr["id"])
        if ov is None:
            continue
        with ov.lock:
            for e in items.all_live_entries(ov.db):
                day = items.expires_on(ov.db, e)
                if day is None or (day - today).days > within:
                    continue
                s = items.summary(ov.db, e, {"id": vr["id"], "name": vr["name"]})
                s["daysLeft"] = (day - today).days
                s["remindDays"] = items.remind_days(ov.db, e)
                out.append(s)
    out.sort(key=lambda x: (x["expires"], search.fold(x["title"])))
    return {"items": out}


def _entry(ov, iid):
    return items.find_entry(ov.db, iid)


@router.get("/vaults/{vid}/items/{iid}")
def get_item(vid: str, iid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
        label = _vault_label(conn, ctx, v)
    with ov.lock:
        e = _entry(ov, iid)
        out = items.detail(ov.db, e, label)
    out["canEdit"] = role in service.EDIT_ROLES
    with db.get_conn() as conn:
        g = conn.execute("SELECT vault_id, item_id FROM guest_wifi").fetchone()
    out["guestWifi"] = bool(g and g["vault_id"] == vid and g["item_id"] == iid)
    _touch_recent(ctx, vid, iid)
    return out


@router.get("/vaults/{vid}/items/{iid}/secret")
def get_secret(vid: str, iid: str, field: str = Query(max_length=100), ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
    _rate_limit_secret(ctx)
    with ov.lock:
        e = _entry(ov, iid)
        value = items.secret(ov.db, e, field)
    _touch_recent(ctx, vid, iid)
    return {"value": value}


@router.get("/vaults/{vid}/items/{iid}/totp")
def get_totp(vid: str, iid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
    with ov.lock:
        p = items.otp_params(ov.db, _entry(ov, iid))
    if p is None:
        raise HTTPException(404, "No 2FA code on this item.")
    return totp.code(p)


@router.get("/vaults/{vid}/items/{iid}/history")
def get_history(vid: str, iid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid)
    with ov.lock:
        return {"history": items.history(ov.db, _entry(ov, iid))}


class ItemIn(Strict):
    type: str | None = Field(default=None, pattern="^(login|note|card)$")
    title: str | None = None
    username: str | None = None
    password: str | None = None
    url: str | None = None
    notes: str | None = None
    totp: str | None = None
    cardholder: str | None = None
    expiry: str | None = None
    card: dict | None = None
    fields: list | None = None
    tags: list | None = None
    favourite: bool | None = None
    folderId: str | None = None
    ifModified: str | None = None
    expires: str | None = Field(default=None, max_length=10)
    remindDays: int | None = None
    template: str | None = Field(default=None, max_length=20)


@router.post("/vaults/{vid}/items", status_code=201)
def create_item(vid: str, body: ItemIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        label = _vault_label(conn, ctx, v)
        with ov.lock:
            g = items.find_group(ov.db, body.folderId)
            if ov.db.in_recycle_bin(g):
                raise HTTPException(409, "You can't add items to the Trash.")
            e = items.create(ov.db, g, body.model_dump(exclude_unset=True))
            service.save(conn, ov, ctx.user["id"])
            return items.detail(ov.db, e, label)


@router.patch("/vaults/{vid}/items/{iid}")
def update_item(vid: str, iid: str, body: ItemIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        label = _vault_label(conn, ctx, v)
        with ov.lock:
            e = _entry(ov, iid)
            if body.ifModified and body.ifModified != ov.db.modified(e):
                who = conn.execute("SELECT u.name FROM vaults v LEFT JOIN users u ON u.id = v.updated_by WHERE v.id = ?",
                                   (vid,)).fetchone()
                raise HTTPException(409, {"message": f"{(who and who['name']) or 'Someone'} changed this item just now.",
                                          "current": items.detail(ov.db, e, label)})
            data = body.model_dump(exclude_unset=True)
            data.pop("ifModified", None)
            folder = data.pop("folderId", None)
            items.apply(ov.db, e, data, creating=False)
            if folder is not None:
                g = items.find_group(ov.db, folder or None)
                if not ov.db.in_recycle_bin(g) and ov.db.parent.get(e) is not g:
                    ov.db.move(e, g)
            service.save(conn, ov, ctx.user["id"])
            return items.detail(ov.db, e, label)


@router.delete("/vaults/{vid}/items/{iid}")
def delete_item(vid: str, iid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            result = items.trash(ov.db, _entry(ov, iid))
            service.save(conn, ov, ctx.user["id"])
    return {"result": result}


@router.post("/vaults/{vid}/restore/{xid}")
def restore(vid: str, xid: str, ctx: Ctx = Depends(unlocked)):
    """Take an item or folder out of the Trash, back where it was."""
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            d = ov.db
            b64 = items.to_b64(xid)
            el = d.entries.get(b64) or d.groups.get(b64)
            if el is None or not d.in_recycle_bin(el) or el is d.recycle_bin():
                raise HTTPException(404, "That isn't in the Trash.")
            items.restore(d, el)
            service.save(conn, ov, ctx.user["id"])
    return {"ok": True}


@router.post("/vaults/{vid}/trash/empty")
def empty_trash(vid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "edit")
        with ov.lock:
            rb = ov.db.recycle_bin()
            if rb is not None:
                for child in [x for x in rb if x.tag in ("Entry", "Group")]:
                    ov.db.delete_permanently(child)
                service.save(conn, ov, ctx.user["id"])
    return {"ok": True}


class MoveIn(Strict):
    items: list[str] = Field(min_length=1, max_length=500)
    toVaultId: str
    toFolderId: str | None = None
    duplicate: bool = False           # copy instead of move


@router.post("/vaults/{vid}/items/move")
def move_items(vid: str, body: MoveIn, ctx: Ctx = Depends(unlocked)):
    """Move or copy items to a folder of this vault or another one (SPEC §9.3)."""
    with db.get_conn() as conn:
        src, v, role = vault_access(conn, ctx, vid, "view" if body.duplicate else "edit")
        dst, v2, role2 = vault_access(conn, ctx, body.toVaultId, "edit")
        first, second = sorted([src, dst], key=lambda o: o.vault_id)
        with first.lock, second.lock:
            target = items.find_group(dst.db, body.toFolderId)
            if dst.db.in_recycle_bin(target):
                raise HTTPException(409, "Use Delete to move items to the Trash.")
            entries = [items.find_entry(src.db, i) for i in body.items]
            moved = 0
            for e in entries:
                if src is dst:
                    if body.duplicate:
                        items.strip_marks(dst.db, src.db.copy_entry_into(dst.db, e, target, new_id=True))
                    elif src.db.parent.get(e) is not target:
                        src.db.move(e, target)
                else:
                    items.strip_marks(dst.db, src.db.copy_entry_into(dst.db, e, target, new_id=True))
                    if not body.duplicate:
                        items.trash(src.db, e)
                moved += 1
            service.save(conn, dst, ctx.user["id"])
            if src is not dst and not body.duplicate:
                service.save(conn, src, ctx.user["id"])
    return {"moved": moved}


# ---------- search ----------
@router.get("/search")
def do_search(q: str = Query(default="", max_length=300), vault: str | None = None, folder: str | None = None,
              type: str | None = Query(default=None, pattern="^(login|note|card)$"), favourites: bool = False,
              ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vaults = _vault_list(conn, ctx.user["id"], ctx.session)
        notes = bool(service.user_row(conn, ctx.user["id"])["search_notes"])
    indexes, locked = [], []
    folder_scope = None
    for vr in vaults:
        if vault and vr["id"] != vault:
            continue
        if not vr["open"]:
            locked.append(vr["name"])
            continue
        ov = sessions.get_open(vr["id"])
        if ov is None:
            continue
        with ov.lock:
            if ov.index is None:
                ov.index = search.build(ov.db, {"id": vr["id"], "name": vr["name"]})
            if folder and vault:
                g = items.find_group(ov.db, folder)
                scope = {items.to_id(x.findtext("UUID")) for x in g.iter("Group")}
                folder_scope = scope | ({""} if g is ov.db.root_group else set())
            indexes.append(({"id": vr["id"], "name": vr["name"]}, ov.index))
    hkeys = None
    if search.health_filters(q):
        from .. import health
        opened = [(v, sessions.get_open(v["id"])) for v, _ in indexes]
        hkeys = health.issue_keys([(v, ov) for v, ov in opened if ov is not None], ctx.session.token)
    results = search.query(indexes, q, notes, _recent_keys(ctx), folder_scope, type, favourites, hkeys)
    return {"items": results, "lockedVaults": locked}


# ---------- scanned / pasted 2FA codes (SPEC §12.2) ----------
class TotpTextIn(Strict):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/totp/parse")
def parse_totp(body: TotpTextIn, ctx: Ctx = Depends(unlocked)):
    """Check a scanned QR text: an otpauth://totp link, a bare secret, or a Google Authenticator export."""
    try:
        out = totp.describe(body.text)
    except totp.BadOtp as x:
        raise HTTPException(422, str(x))
    # say which codes you already have (same secret in an open vault), so they aren't added twice
    have = _known_secrets(ctx)
    for x in out.get("entries", []) + ([out] if out.get("kind") == "totp" else []):
        try:
            sec = totp.parse(x["uri"])["secret"] if x.get("uri") else None
        except (totp.BadOtp, KeyError):
            sec = None
        x["existing"] = have.get(sec) if sec else None
    return out


def _known_secrets(ctx: Ctx) -> dict:
    """{TOTP secret: "Title (Vault)"} across the open vaults."""
    with db.get_conn() as conn:
        vaults = [v for v in _vault_list(conn, ctx.user["id"], ctx.session) if v["open"]]
    out = {}
    for vr in vaults:
        ov = sessions.get_open(vr["id"])
        if ov is None:
            continue
        with ov.lock:
            for e in items.all_live_entries(ov.db):
                p = items.otp_params(ov.db, e)
                if p:
                    out.setdefault(p["secret"], f"{ov.db.get_field(e, 'Title') or '(no name)'} ({vr['name']})")
    return out
