"""Vaults: list, create, share, re-key, passwords, versions, downloads, import (SPEC §5, §8)."""
import base64
import binascii
import re
import threading
import urllib.parse
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import Field

from .. import alerts, db, importers, items, kdbx, service, sessions, storage
from .common import Ctx, Strict, unlocked, vault_access
from .me import _vault_list, _vault_out

router = APIRouter(prefix="/api", tags=["vaults"])


@router.get("/vaults")
def list_vaults(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        return {"vaults": _vault_list(conn, ctx.user["id"], ctx.session)}


class CreateIn(Strict):
    name: str = Field(min_length=1, max_length=60)
    passwordMode: str = Field(default="random", pattern="^(random|chosen)$")
    password: str | None = Field(default=None, max_length=1000)


def _name(n: str) -> str:
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        raise HTTPException(422, "Give the vault a name.")
    return n


@router.post("/vaults", status_code=201)
def create_vault(body: CreateIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        with service.editing(conn):                   # a failure leaves no vault, folder or open vault behind
            vid = service.create_shared(conn, ctx.session, _name(body.name), body.passwordMode, body.password)
        return _vault_out(conn, dict(service.vault_row(conn, vid), role="owner"), ctx.session, ctx.user["id"])


class RenameIn(Strict):
    name: str = Field(min_length=1, max_length=60)


@router.patch("/vaults/{vid}")
def rename_vault(vid: str, body: RenameIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "manage")
        if v["kind"] != "shared":
            raise HTTPException(409, "Personal and Household can't be renamed.")
        name = _name(body.name)
        with service.editing(conn, ov):               # the vault's lock first, then the first write
            conn.execute("UPDATE vaults SET name = ? WHERE id = ?", (name, vid))
            ov.db.set_name(name)
            service.save(conn, ov, ctx.user["id"])
        return {"ok": True}


class DeleteIn(Strict):
    confirmName: str


@router.post("/vaults/{vid}/delete")
def delete_vault(vid: str, body: DeleteIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "own", must_be_open=False)
        if v["kind"] != "shared":
            raise HTTPException(409, "Personal and Household can't be deleted.")
        if body.confirmName.strip() != v["name"]:
            raise HTTPException(422, "Type the vault's name exactly to delete it.")
        sessions.close(vid)
        conn.execute("DELETE FROM vaults WHERE id = ?", (vid,))
        conn.execute("DELETE FROM recent_items WHERE vault_id = ?", (vid,))
        storage.delete_vault_files(conn, vid)             # once the delete has committed
        db.audit(conn, "deleted", ctx.user["id"], vid)
    return {"ok": True}


class ShareIn(Strict):
    userId: str
    role: str = Field(default="editor", pattern="^(manager|editor|viewer)$")


@router.post("/vaults/{vid}/members")
def share(vid: str, body: ShareIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "manage")
        service.share(conn, ctx.session, vid, body.userId, body.role)
        return _vault_out(conn, dict(service.vault_row(conn, vid), role=role), ctx.session, ctx.user["id"])


@router.delete("/vaults/{vid}/members/{uid}")
def unshare(vid: str, uid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "manage")
        target = service.role_of(conn, vid, uid)
        if target is None:
            raise HTTPException(404, "They don't have access.")
        if target == "owner":
            raise HTTPException(409, "The owner can't be removed. Transfer ownership first.")
        if v["kind"] == "household":
            raise HTTPException(409, "Household is everyone's — an admin can turn someone's access off instead.")
        service.remove_member(conn, vid, uid, ctx.user["id"])
        return _vault_out(conn, dict(service.vault_row(conn, vid), role=role), ctx.session, ctx.user["id"])


@router.post("/vaults/{vid}/leave")
def leave(vid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "view", must_be_open=False)
        if role == "owner":
            raise HTTPException(409, "You own this vault — transfer it or delete it instead.")
        if v["kind"] == "household":
            raise HTTPException(409, "Everyone is in Household.")
        if v["kind"] == "emergency":
            raise HTTPException(409, "These are someone's emergency items — ask them to remove you instead.")
        service.remove_member(conn, vid, ctx.user["id"], ctx.user["id"], audit_action="left")
    return {"ok": True}


class TransferIn(Strict):
    userId: str


@router.post("/vaults/{vid}/transfer")
def transfer(vid: str, body: TransferIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vault_access(conn, ctx, vid, "own", must_be_open=False)
        service.transfer(conn, ctx.session, vid, body.userId)
    return {"ok": True}


class VaultPasswordIn(Strict):
    new: str = Field(min_length=1, max_length=1000)
    remember: bool = True


@router.post("/vaults/{vid}/password")
def change_vault_password(vid: str, body: VaultPasswordIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vault_access(conn, ctx, vid, "own")
        service.change_vault_password(conn, ctx.session, vid, body.new, body.remember)
    return {"ok": True}


@router.post("/vaults/{vid}/rekey")
def rekey(vid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "view")
        if v["password_mode"] != "random":
            raise HTTPException(409, "This vault has a chosen password — change the password instead.")
        if v["kind"] == "household" and not ctx.user["is_admin"]:
            # review 2026-10: any member could re-key Household (and so wipe its history); now only an admin
            raise HTTPException(403, "Only an admin can re-key Household.")
        if v["kind"] != "household" and role not in service.MANAGE_ROLES:
            raise HTTPException(403, "Only the vault's owner or a manager can do this.")
        if v["kind"] == "emergency" and role != "owner":
            raise HTTPException(403, "Only the owner can do this.")
        service.rekey(conn, ov, ctx.user["id"])
    return {"ok": True}


@router.get("/vaults/{vid}/password")
def show_password(vid: str, ctx: Ctx = Depends(unlocked)):
    """A random-password vault's password, so a member can open the file in KeePassXC."""
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, "view")
        if v["password_mode"] != "random":
            raise HTTPException(409, "This vault's password is the one you typed.")
        db.audit(conn, "password_shown", ctx.user["id"], vid)
        alerts.send(conn, alerts.others_in(conn, vid, ctx.user["id"]),
                    f"{ctx.user['name']} looked at the password of {alerts.vault_label(conn, vid)} "
                    "(to open it in another KeePass app).")
        return {"password": ov.password}


class OpenIn(Strict):
    password: str = Field(min_length=1, max_length=1000)
    remember: bool = True


@router.post("/vaults/{vid}/open")
def open_vault(vid: str, body: OpenIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        vault_access(conn, ctx, vid, "view", must_be_open=False)
        service.open_with_password(conn, ctx.session, vid, body.password, body.remember)
        return _vault_out(conn, dict(service.vault_row(conn, vid), role=service.role_of(conn, vid, ctx.user["id"])),
                          ctx.session, ctx.user["id"])


@router.post("/vaults/{vid}/forget")
def forget(vid: str, ctx: Ctx = Depends(unlocked)):
    """Stop opening this vault with my master password (I'll type its password next time)."""
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "view", must_be_open=False)
        if v["kind"] == "household" or v["password_mode"] == "random":
            raise HTTPException(409, "This vault's password is random — it always opens with your master password.")
        service.forget(conn, vid, ctx.user["id"])
    return {"ok": True}


def _history_level(conn, vid: str) -> str:
    """Household: any member; other vaults: owner or manager (Personal: its owner)."""
    v = service.vault_row(conn, vid)
    return "edit" if v is not None and v["kind"] == "household" else "manage"


@router.get("/vaults/{vid}/versions")
def versions(vid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, _history_level(conn, vid))
        rows = conn.execute("SELECT vv.version, vv.size, vv.created_at, u.name FROM vault_versions vv "
                            "LEFT JOIN users u ON u.id = vv.created_by WHERE vv.vault_id = ? ORDER BY vv.version DESC",
                            (vid,)).fetchall()
        return {"current": v["version"], "versions": [{"version": r["version"], "size": r["size"],
                                                        "createdAt": r["created_at"], "by": r["name"]} for r in rows]}


@router.post("/vaults/{vid}/versions/{n}/restore")
def restore_version(vid: str, n: int, ctx: Ctx = Depends(unlocked)):
    """Only versions since the last password change are kept, so the current password opens them (SPEC §7)."""
    with db.get_conn() as conn:
        ov, v, role = vault_access(conn, ctx, vid, _history_level(conn, vid))
        row = conn.execute("SELECT key_epoch FROM vault_versions WHERE vault_id = ? AND version = ?", (vid, n)).fetchone()
        if row is None:
            raise HTTPException(404, "That version isn't kept any more.")
        if n == v["version"]:
            raise HTTPException(409, "That's the current version.")
        with service.editing(conn, ov):
            old = kdbx.open_with_composite(storage.read_version(vid, n), ov.composite,
                                           known=(ov.db.kdf, ov.db.transformed))
            ov.db = old
            service.save(conn, ov, ctx.user["id"])
            db.audit(conn, "restored", ctx.user["id"], vid)
    return {"ok": True}


def _filename(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "vault"
    return f"{safe}-{date.today().isoformat()}.kdbx"


@router.get("/vaults/{vid}/download")
def download(vid: str, ctx: Ctx = Depends(unlocked)):
    """The current file, still encrypted with the vault's password."""
    with db.get_conn() as conn:
        _ov, v, role = vault_access(conn, ctx, vid, "view")
        data = storage.read_current(conn, vid)
        db.audit(conn, "downloaded", ctx.user["id"], vid)
        alerts.send(conn, alerts.others_in(conn, vid, ctx.user["id"]),
                    f"{ctx.user['name']} downloaded a copy of {alerts.vault_label(conn, vid)} (still encrypted).")
    name = "Personal" if v["kind"] == "personal" else v["name"]
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{_filename("household-vault-" + name)}"'})


class DownloadAllIn(Strict):
    password: str = Field(min_length=1, max_length=1000)
    includeDeleted: bool = False


@router.post("/me/download-all")
def download_all(body: DownloadAllIn, ctx: Ctx = Depends(unlocked)):
    """One .kdbx with everything I have open, locked with my master password (SPEC §12.6)."""
    with db.get_conn() as conn:
        personal = service.personal_of(conn, ctx.user["id"])
        pov = sessions.get_open(personal["id"])
        service.check_master_password(conn, ctx.user["id"], pov, body.password, "That isn't your master password.")
        combined = kdbx.Database.create(f"Household Vault — {ctx.user['name']}")
        combined.kdf = kdbx.kdf_params(service.strong_kdf(conn))
        rows = _vault_list(conn, ctx.user["id"], ctx.session)
        locked = []
        for vr in rows:
            if not vr["open"]:
                locked.append(vr["name"])
                continue
            ov = sessions.get_open(vr["id"])
            with ov.lock:
                if vr["id"] == personal["id"]:
                    _copy_tree(ov.db, ov.db.root_group, combined, combined.root_group, body.includeDeleted)
                else:
                    g = combined.add_group(combined.root_group, vr["name"])
                    _copy_tree(ov.db, ov.db.root_group, combined, g, body.includeDeleted)
        combined.reindex()
        items.sync_keepass_otp(combined)          # KeePass 2.47+ reads TimeOtp-*; the others read `otp`
        data = kdbx.save_bytes(combined, body.password, new_salt=True)
        db.audit(conn, "downloaded_all", ctx.user["id"])
        alerts.send(conn, [ctx.user["id"]], "A copy of all your passwords was just downloaded from Household Vault. "
                    "If it wasn't you, change your master password.")
    first = (ctx.user["name"] or "me").split()[0]
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{_filename("household-vault-" + first)}"',
        # percent-encoded (UTF-8): a header can only carry Latin-1, and a vault may be called "Família" or "家"
        "X-Locked-Vaults": ";".join(urllib.parse.quote(x, safe=" ") for x in locked)})


def _copy_tree(src: kdbx.Database, src_group, dst: kdbx.Database, dst_group, include_deleted: bool, skip=None) -> None:
    import xml.etree.ElementTree as ET
    rb = src.recycle_bin()
    for child in list(src_group):
        if child.tag == "Entry":
            if skip is None or not skip(child):
                # an import into a vault that already has this id (the same file imported twice): a new id, or the
                # two items would share one and only one could be found
                clash = skip is not None and child.findtext("UUID") in dst.entries
                clone = src.copy_entry_into(dst, child, dst_group, new_id=clash)
                if skip is not None:                  # an import (not Download all): drop this household's own marks
                    items.strip_marks(dst, clone)
        elif child.tag == "Group":
            if child is rb and not include_deleted:
                continue
            g = ET.fromstring(ET.tostring(child))
            for x in list(g):
                if x.tag in ("Entry", "Group"):
                    g.remove(x)
            if skip is not None and g.findtext("UUID") in dst.groups:
                g.find("UUID").text = kdbx.new_uuid()
            dst.move(g, dst_group)
            dst.reindex()
            _copy_tree(src, child, dst, g, include_deleted, skip)


def _valid_uuid(text: str | None) -> bool:
    try:
        return len(base64.b64decode(text or "", validate=True)) == 16
    except (binascii.Error, ValueError):
        return False


def fix_ids(d: kdbx.Database) -> int:
    """An imported file's entries and folders whose UUID is missing, malformed or used twice get a new one — the
    app's ids are those UUIDs, and one it can't read used to break every listing of the vault (review 2026-10).
    History copies keep theirs. Returns how many were changed."""
    import xml.etree.ElementTree as ET
    n, seen = 0, set()
    stack = [d.root_group]
    while stack:
        el = stack.pop()
        node = el.find("UUID")
        if node is None:
            node = ET.Element("UUID")
            el.insert(0, node)
        if not _valid_uuid(node.text) or node.text in seen:
            node.text = kdbx.new_uuid()
            n += 1
        seen.add(node.text)
        stack.extend(x for x in el if x.tag in ("Group", "Entry"))
    if n:
        d.reindex()
    return n


def _prune_empty(d: kdbx.Database, g) -> None:
    """Drop folders an import left empty (everything in them was a duplicate)."""
    for child in [x for x in g if x.tag == "Group"]:
        _prune_empty(d, child)
    if g is not d.root_group and not any(x.tag in ("Entry", "Group") for x in g):
        parent = d.parent.get(g)
        if parent is not None:
            parent.remove(g)
            d.reindex()


# Opening a file from elsewhere runs its key derivation (up to kdbx.MAX_ARGON2_MEMORY of memory, seconds of work):
# one at a time, so a few uploads at once can't take all the memory or every worker thread (review 2026-10)
_IMPORT_SLOT = threading.BoundedSemaphore(1)
IMPORT_WAIT_S = 30


@router.post("/import", status_code=201)
async def import_file(file: UploadFile = File(...), password: str = Form(""), keyfile: UploadFile | None = File(None),
                      target: str = Form("new"), name: str = Form(""), skipDuplicates: bool = Form(True),
                      ctx: Ctx = Depends(unlocked)):
    """A .kdbx from another KeePass app, or a CSV export (Chrome, Firefox, Bitwarden, LastPass, 1Password…):
    as a new shared vault (random password), or into a folder of a vault I can edit (`target` = its id).
    With `skipDuplicates`, items already in one of my open vaults (same name, username, website and password)
    are left out."""
    data = await file.read(storage.MAX_FILE_BYTES + 1)
    if len(data) > storage.MAX_FILE_BYTES:
        raise HTTPException(413, "That file is bigger than 20 MB.")
    kf = await keyfile.read(1024 * 1024 + 1) if keyfile is not None else None
    if kf is not None and len(kf) > 1024 * 1024:
        raise HTTPException(413, "That key file is bigger than 1 MB.")
    # opening the file runs its key derivation (seconds of work) and saving re-encrypts: off the event loop,
    # so the rest of the app keeps answering meanwhile
    return await run_in_threadpool(_import, data, kf, file.filename, password, target, name, skipDuplicates, ctx)


def _open_import(data: bytes, password: str, kf: bytes | None) -> kdbx.Database:
    if not _IMPORT_SLOT.acquire(timeout=IMPORT_WAIT_S):
        raise HTTPException(429, "Another file is being opened right now — try again in a minute.")
    try:
        src = kdbx.open_bytes(data, password or None, kf)
    except kdbx.WrongKey:
        raise HTTPException(403, "That password (or key file) doesn't open the file.")
    except kdbx.KdbxError as e:
        raise HTTPException(422, str(e))
    except (*kdbx.DAMAGED, MemoryError):
        raise HTTPException(422, "The file is damaged.")
    finally:
        _IMPORT_SLOT.release()
    fix_ids(src)
    return src


def _import(data: bytes, kf: bytes | None, filename: str | None, password: str, target: str, name: str,
            skipDuplicates: bool, ctx: Ctx) -> dict:
    src, records = None, None
    if kdbx.is_kdbx(data[:12]):
        src = _open_import(data, password, kf)
    elif importers.looks_like_csv(data):
        records = importers.parse_csv(data)
    else:
        raise HTTPException(422, "That isn't a KeePass file (.kdbx) or a CSV export.")
    added = skipped = 0
    with db.get_conn() as conn:
        seen = None
        if skipDuplicates:
            seen = set()
            for vr in _vault_list(conn, ctx.user["id"], ctx.session):
                ov = sessions.get_open(vr["id"]) if vr["open"] else None
                if ov is not None:
                    with ov.lock:
                        seen |= importers.signatures_of(ov.db)

        def dup(e) -> bool:
            nonlocal skipped
            if seen is None:
                return False
            sig = importers.entry_signature(src, e)
            if sig in seen:
                skipped += 1
                return True
            seen.add(sig)
            return False

        default_name = (filename or "Imported").rsplit(".", 1)[0]
        if target == "new":
            vname = _name(name or (src.name if src else "") or default_name)[:60]
            # one transaction: if the import can't be saved, the new vault — its row, its folder and the open
            # vault — goes too (review 2026-10)
            with service.editing(conn):
                vid = service.create_shared(conn, ctx.session, vname, "random", None)
                ov = sessions.get_open(vid)
                with service.editing(conn, ov):
                    if src is not None:
                        for e in list(src.entries.values()):
                            if not src.in_recycle_bin(e) and dup(e):
                                src.parent[e].remove(e)
                        src.reindex()
                        for e in src.entries.values():
                            items.strip_marks(src, e)
                        src.set_name(vname)
                        src.kdf, src.transformed, src.composite = ov.db.kdf, ov.db.transformed, ov.db.composite
                        ov.db = src
                        added = len(items.all_live_entries(src))
                    else:
                        added, skipped = importers.add_records(ov.db, ov.db.root_group, records, seen)
                    service.save(conn, ov, ctx.user["id"])
        else:
            ov, v, role = vault_access(conn, ctx, target, "edit")
            vid = target
            with service.editing(conn, ov):
                label = (name or f"Imported {date.today().isoformat()}")[:60]
                g = ov.db.add_group(ov.db.root_group, label)
                if src is not None:
                    before = len(ov.db.entries)
                    _copy_tree(src, src.root_group, ov.db, g, False, dup if seen is not None else (lambda e: False))
                    ov.db.reindex()
                    added = len(ov.db.entries) - before
                else:
                    added, skipped = importers.add_records(ov.db, g, records, seen)
                _prune_empty(ov.db, g)
                if added:
                    service.save(conn, ov, ctx.user["id"])
                else:
                    ov.db.reindex()
        db.audit(conn, "imported", ctx.user["id"], vid)
    return {"vaultId": vid, "items": added, "skipped": skipped, "keyFileDropped": kf is not None,
            "format": "kdbx" if src is not None else "csv"}
