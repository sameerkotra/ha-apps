"""Photos and documents (§5, §11): profile photos, the gallery, links to people,
families and events, and serving the files from /share."""
import io
import json
import logging
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .. import names as names_mod, dates, db, features, media, photoedit, settings
from ..auth import require_user
from ..common import DateIn, Strict, clean, date_cols, require_person
from ..history import Batch
from .people import _nm, person_detail

router = APIRouter(prefix="/api", tags=["media"])
logger = logging.getLogger("media")


MAX_LINKS_PER_PERSON = 200


async def _read_upload(file: UploadFile, allow_documents: bool) -> tuple[bytes, str]:
    limit = settings.get("max_upload_mb") * 1024 * 1024
    data = await file.read(limit + 1)
    try:
        ctype = media.validate_upload(data, allow_documents=allow_documents)
    except media.MediaError as e:
        raise HTTPException(e.status, str(e))
    media.require_online()
    return data, ctype


async def _process(data: bytes, ctype: str) -> dict:
    """Images are re-encoded (metadata stripped); PDFs are kept as they are."""
    if ctype == "application/pdf":
        return {"content_type": ctype, "original": data, "thumbs": {}, "width": None, "height": None, "date_text": None,
                "document": True}
    try:
        return await run_in_threadpool(media.process_image, data)
    except media.MediaError as e:
        raise HTTPException(e.status, str(e))


def _store(mid: str, processed: dict) -> None:
    if processed.get("document"):
        media.store_document(mid, processed["original"])
    else:
        media.store_image(mid, processed)


def _check_link_target(conn, person_id=None, family_id=None, event_id=None) -> None:
    if sum(bool(x) for x in (person_id, family_id, event_id)) != 1:
        raise HTTPException(422, "Link to exactly one person, family or event.")
    if person_id:
        require_person(conn, person_id)
        n = conn.execute("SELECT COUNT(*) FROM media_links WHERE person_id = ?", (person_id,)).fetchone()[0]
        if n >= MAX_LINKS_PER_PERSON:
            raise HTTPException(422, f"A person can have at most {MAX_LINKS_PER_PERSON} photos and documents.")
    elif family_id:
        from ..common import require_family
        require_family(conn, family_id)
    else:
        if not conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
            raise HTTPException(404, "That event doesn't exist.")


def _insert_media(b: Batch, mid: str, processed: dict, data: bytes, title: str | None, user: dict) -> None:
    b.insert("media", {"id": mid, "kind": "document" if processed.get("document") else "photo",
                       "title": title, "date_text": processed["date_text"], "content_type": processed["content_type"],
                       "size": len(processed["original"]), "sha256": media.sha256(data),
                       "width": processed["width"], "height": processed["height"],
                       "created_by": user["id"], "created_at": b.now})


@router.post("/people/{pid}/photo", status_code=201)
async def upload_photo(pid: str, file: UploadFile = File(...), user: dict = Depends(require_user)):
    """Upload a new profile photo (it also joins the person's photos)."""
    data, ctype = await _read_upload(file, allow_documents=False)
    processed = await _process(data, ctype)
    return await run_in_threadpool(_save_photo, pid, processed, data, user)


def _save_photo(pid, processed, data, user):
    mid = db.new_id()
    with db.get_conn() as conn:
        person = require_person(conn, pid)
    _store(mid, processed)
    try:
        with db.get_conn() as conn:
            b = Batch(conn, user["id"], f"New photo for {_nm(person)}")
            _insert_media(b, mid, processed, data, f"Photo of {_nm(person)}", user)
            b.insert("media_links", {"id": db.new_id(), "media_id": mid, "person_id": pid}, op="link")
            b.update("people", pid, {"photo_media_id": mid, "photo_region_id": None})
            b.touch(pid)
            return {"person": person_detail(conn, pid, user["me_person_id"]), "mediaId": mid, "batchId": b.id}
    except Exception:
        media.remove_files(mid)          # never leave files the database doesn't know about
        raise


class ProfilePick(Strict):
    mediaId: str
    regionId: str | None = None           # crop to this tag box (§13.2); it must be this person's box


@router.put("/people/{pid}/photo")
def choose_photo(pid: str, body: ProfilePick, user: dict = Depends(require_user)):
    """Use one of the existing photos as the profile photo (linking it if needed)."""
    with db.get_conn() as conn:
        person = require_person(conn, pid)
        row = conn.execute("SELECT * FROM media WHERE id = ?", (body.mediaId,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That photo doesn't exist.")
        if row["kind"] != "photo":
            raise HTTPException(422, "Only a photo can be a profile photo.")
        if body.regionId:
            features.check("photo_tagging", user)
            rg = conn.execute("SELECT * FROM media_regions WHERE id = ?", (body.regionId,)).fetchone()
            if not rg or rg["media_id"] != body.mediaId or rg["person_id"] != pid:
                raise HTTPException(422, "That box isn't around this person on this photo.")
        b = Batch(conn, user["id"], f"Changed the photo of {_nm(person)}")
        if not conn.execute("SELECT 1 FROM media_links WHERE media_id = ? AND person_id = ?", (body.mediaId, pid)).fetchone():
            _check_link_target(conn, person_id=pid)
            b.insert("media_links", {"id": db.new_id(), "media_id": body.mediaId, "person_id": pid}, op="link")
        b.update("people", pid, {"photo_media_id": body.mediaId, "photo_region_id": body.regionId})
        b.touch(pid)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "batchId": b.id}


# ---------- the gallery ----------


def _date_view(date_text):
    if not date_text:
        return None, None, None
    try:
        cols = dates.parse_text(date_text)
    except dates.DateError:
        return None, date_text, None
    return dates.to_parts(cols), dates.display(cols), cols["sort_key"]


def _edit_of(r):
    return json.loads(r["edit"]) if r["edit"] else None


def _shown(rg, mrow) -> dict:
    """A tag box as it appears on the (possibly edited) picture (§13.19)."""
    box = {"x": rg["x"], "y": rg["y"], "w": rg["w"], "h": rg["h"]}
    edit = _edit_of(mrow)
    if edit and mrow["width"] and mrow["height"]:
        box = photoedit.box_to_display(box, edit, mrow["width"], mrow["height"])
        box = {k: round(v, 5) for k, v in box.items()}
        box["visible"] = photoedit.visible(box)
    else:
        box["visible"] = True
    return box


def _suggestable(conn):
    return [(r["id"], names_mod.row_name(r), (r["given_names"] or "").lower(), (r["nickname"] or "").lower())
            for r in conn.execute("SELECT id, given_names, surname, nickname, name_order FROM people WHERE deleted_at IS NULL")]


def _suggest(r, people):
    """An inbox subfolder named after someone suggests them (§13.10)."""
    rel = (r["orig_name"] or "").split("/")
    if len(rel) < 2:
        return None
    folder = rel[0].strip().lower()
    for pid, name, given, nick in people:
        if folder in (name.lower(), given, nick) and folder:
            return {"id": pid, "name": name}
    return None


def media_out(conn, rows) -> list:
    """Media rows → API items with their links (names resolved) and profile use."""
    rows = list(rows)
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    q = ",".join("?" * len(ids))
    links = {}
    for l in conn.execute(f"SELECT * FROM media_links WHERE media_id IN ({q})", ids):
        links.setdefault(l["media_id"], []).append(dict(l))
    fam_rows = {}
    fids = {l["family_id"] for ls in links.values() for l in ls if l["family_id"]}
    if fids:
        fq = ",".join("?" * len(fids))
        fam_rows = {r["id"]: r for r in conn.execute(f"SELECT id, partner1_id, partner2_id FROM families WHERE id IN ({fq})", list(fids))}
    ev_rows = {}
    eids = {l["event_id"] for ls in links.values() for l in ls if l["event_id"]}
    if eids:
        eq = ",".join("?" * len(eids))
        ev_rows = {r["id"]: r for r in conn.execute(f"SELECT id, type, title, person_id, family_id FROM events WHERE id IN ({eq})", list(eids))}
    pids = {l["person_id"] for ls in links.values() for l in ls if l["person_id"]}
    pids |= {x for f in fam_rows.values() for x in (f["partner1_id"], f["partner2_id"]) if x}
    pids |= {e["person_id"] for e in ev_rows.values() if e["person_id"]}
    suggest_people = _suggestable(conn) if any(r["unsorted"] for r in rows) else []
    regions = {}
    for rg in conn.execute(f"SELECT * FROM media_regions WHERE media_id IN ({q}) ORDER BY created_at, id", ids):
        regions.setdefault(rg["media_id"], []).append(dict(rg))
    pids |= {rg["person_id"] for rs in regions.values() for rg in rs if rg["person_id"]}
    names = names_mod.people_names(conn, pids, include_deleted=False)
    profile, profile_region = {}, {}
    for r in conn.execute(f"SELECT id, photo_media_id, photo_region_id FROM people WHERE photo_media_id IN ({q}) "
                          "AND deleted_at IS NULL", ids):
        profile.setdefault(r["photo_media_id"], []).append(r["id"])
        if r["photo_region_id"]:
            profile_region[r["photo_region_id"]] = r["id"]
    tagging, inbox_on = features.on("photo_tagging"), features.on("inbox")
    out = []
    for r in rows:
        parts, disp, key = _date_view(r["date_text"])
        items = []
        for l in links.get(r["id"], []):
            if l["person_id"]:
                if l["person_id"] not in names:
                    continue                                  # a trashed person
                items.append({"id": l["id"], "personId": l["person_id"], "label": names[l["person_id"]]})
            elif l["family_id"]:
                f = fam_rows.get(l["family_id"])
                if f:
                    label = " & ".join(names[x] for x in (f["partner1_id"], f["partner2_id"]) if x in names) or "a family"
                    items.append({"id": l["id"], "familyId": l["family_id"], "label": f"Family of {label}"})
            elif l["event_id"]:
                e = ev_rows.get(l["event_id"])
                if e:
                    who = names.get(e["person_id"], "")
                    what = e["title"] or e["type"]
                    items.append({"id": l["id"], "eventId": l["event_id"], "personId": e["person_id"],
                                  "label": what[:1].upper() + what[1:] + (f" — {who}" if who else "")})
        out.append({
            "id": r["id"], "kind": r["kind"], "title": r["title"], "description": r["description"],
            "date": parts, "dateText": r["date_text"], "dateDisplay": disp, "_sort": key,
            "contentType": r["content_type"], "size": r["size"], "width": r["width"], "height": r["height"],
            "createdAt": r["created_at"], "deleted": bool(r["deleted_at"]),
            "links": items, "profileOf": profile.get(r["id"], []),
            "edit": _edit_of(r), "unsorted": bool(r["unsorted"]), "origName": r["orig_name"],
            "suggest": _suggest(r, suggest_people) if r["unsorted"] and inbox_on else None,
            "regions": [] if not tagging else [dict(_shown(rg, r), id=rg["id"], personId=rg["person_id"], name=names[rg["person_id"]],
                             profile=profile_region.get(rg["id"]) == rg["person_id"])
                        for rg in regions.get(r["id"], []) if rg["person_id"] in names],
        })
    return out


@router.get("/media")
def list_media(personId: str | None = None, familyId: str | None = None, kind: str | None = None,
               q: str | None = None, unlinked: bool = False, unsorted: bool | None = None, sort: str = "added",
               page: int = Query(1, ge=1, le=100000), page_size: int = Query(60, ge=1, le=200),
               user: dict = Depends(require_user)):
    where, args = ["m.deleted_at IS NULL"], []
    if kind in ("photo", "document"):
        where.append("m.kind = ?")
        args.append(kind)
    if personId:
        where.append("m.id IN (SELECT media_id FROM media_links WHERE person_id = ? UNION "
                     "SELECT media_id FROM media_links ml JOIN events e ON e.id = ml.event_id WHERE e.person_id = ?)")
        args += [personId, personId]
    if familyId:
        where.append("m.id IN (SELECT media_id FROM media_links WHERE family_id = ?)")
        args.append(familyId)
    if unlinked:
        where.append("m.id NOT IN (SELECT media_id FROM media_links)")
    if unsorted is not None:
        features.check("inbox", user)
        where.append("m.unsorted = ?")
        args.append(1 if unsorted else 0)
    if q and q.strip():
        where.append("(m.title LIKE ? OR m.description LIKE ?)")
        args += [f"%{q.strip()}%"] * 2
    with db.get_conn() as conn:
        rows = conn.execute(f"SELECT m.* FROM media m WHERE {' AND '.join(where)} ORDER BY m.created_at DESC, m.rowid DESC",
                            args).fetchall()
        items = media_out(conn, rows)
    if sort == "date":
        items.sort(key=lambda m: (m["_sort"] is None, m["_sort"] or ""))
    total = len(items)
    chunk = items[(page - 1) * page_size: page * page_size]
    for m in items:
        m.pop("_sort", None)
    return {"items": chunk, "total": total, "page": page, "pageSize": page_size}


@router.get("/media/{mid}")
def get_media(mid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "That photo doesn't exist.")
        item = media_out(conn, [row])[0]
    item.pop("_sort", None)
    return item


@router.post("/media", status_code=201)
async def upload_media(file: UploadFile = File(...), personId: str | None = Form(None), familyId: str | None = Form(None),
                       eventId: str | None = Form(None), title: str | None = Form(None),
                       user: dict = Depends(require_user)):
    """One photo or PDF per request (the browser sends several in a row)."""
    data, ctype = await _read_upload(file, allow_documents=True)
    processed = await _process(data, ctype)
    guess = clean(title) or clean(os.path.splitext(os.path.basename(file.filename or ""))[0].replace("_", " ")) or None
    return await run_in_threadpool(_save_upload, processed, data, guess, personId, familyId, eventId, user)


def _save_upload(processed, data, title, person_id, family_id, event_id, user):
    mid = db.new_id()
    with db.get_conn() as conn:
        if person_id or family_id or event_id:
            _check_link_target(conn, person_id, family_id, event_id)
    _store(mid, processed)
    try:
        with db.get_conn() as conn:
            word = "document" if processed.get("document") else "photo"
            b = Batch(conn, user["id"], f"Added a {word}")
            _insert_media(b, mid, processed, data, (title or "")[:200] or None, user)
            if person_id or family_id or event_id:
                b.insert("media_links", {"id": db.new_id(), "media_id": mid, "person_id": person_id,
                                         "family_id": family_id, "event_id": event_id}, op="link")
                if person_id:
                    b.touch(person_id)
                    who = conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
                    conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Added a {word} of {_nm(who)}", b.id))
            item = media_out(conn, [conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()])[0]
            item.pop("_sort", None)
            return {"media": item, "batchId": b.id}
    except Exception:
        media.remove_files(mid)
        raise


class MediaPatch(Strict):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    date: DateIn | None = None


@router.patch("/media/{mid}")
def patch_media(mid: str, body: MediaPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That photo doesn't exist.")
        sent = body.model_fields_set
        fields = {}
        if "title" in sent:
            fields["title"] = clean(body.title)
        if "description" in sent:
            fields["description"] = (body.description or "").strip() or None
        if "date" in sent:
            fields["date_text"] = date_cols(body.date)["date_text"]
        b = Batch(conn, user["id"], f"Edited {'a document' if row['kind'] == 'document' else 'a photo'}")
        b.update("media", mid, fields)
        for l in conn.execute("SELECT person_id FROM media_links WHERE media_id = ? AND person_id IS NOT NULL", (mid,)):
            b.touch(l["person_id"])
        item = media_out(conn, [conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()])[0]
        item.pop("_sort", None)
        return {"media": item, "batchId": b.id}


@router.delete("/media/{mid}")
def delete_media(mid: str, user: dict = Depends(require_user)):
    """To the trash. Anyone using it as a profile photo goes back to initials."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That photo doesn't exist.")
        b = Batch(conn, user["id"], f"Deleted {'a document' if row['kind'] == 'document' else 'a photo'}")
        for p in conn.execute("SELECT id FROM people WHERE photo_media_id = ?", (mid,)).fetchall():
            b.update("people", p["id"], {"photo_media_id": None, "photo_region_id": None})
            b.touch(p["id"])
        for l in conn.execute("SELECT person_id FROM media_links WHERE media_id = ? AND person_id IS NOT NULL", (mid,)):
            b.touch(l["person_id"])
        b.soft_delete("media", mid)
        return {"ok": True, "batchId": b.id}


class LinkIn(Strict):
    personId: str | None = None
    familyId: str | None = None
    eventId: str | None = None


@router.post("/media/{mid}/links", status_code=201)
def add_link(mid: str, body: LinkIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That photo doesn't exist.")
        _check_link_target(conn, body.personId, body.familyId, body.eventId)
        col, val = next((c, v) for c, v in (("person_id", body.personId), ("family_id", body.familyId),
                                           ("event_id", body.eventId)) if v)
        if conn.execute(f"SELECT 1 FROM media_links WHERE media_id = ? AND {col} = ?", (mid, val)).fetchone():
            raise HTTPException(409, "It's already linked there.")
        b = Batch(conn, user["id"], f"Linked {'a document' if row['kind'] == 'document' else 'a photo'}")
        b.insert("media_links", {"id": db.new_id(), "media_id": mid, col: val}, op="link")
        if body.personId:
            b.touch(body.personId)
            who = conn.execute("SELECT * FROM people WHERE id = ?", (body.personId,)).fetchone()
            conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Tagged {_nm(who)} in a {row['kind']}", b.id))
        item = media_out(conn, [conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()])[0]
        item.pop("_sort", None)
        return {"media": item, "batchId": b.id}


@router.delete("/media/{mid}/links/{link_id}")
def remove_link(mid: str, link_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        link = conn.execute("SELECT * FROM media_links WHERE id = ? AND media_id = ?", (link_id, mid)).fetchone()
        if not link:
            raise HTTPException(404, "That link doesn't exist.")
        b = Batch(conn, user["id"], "Removed a photo link")
        if link["person_id"]:
            p = conn.execute("SELECT * FROM people WHERE id = ?", (link["person_id"],)).fetchone()
            if p and p["photo_media_id"] == mid:
                b.update("people", p["id"], {"photo_media_id": None, "photo_region_id": None})
            for rg in conn.execute("SELECT id FROM media_regions WHERE media_id = ? AND person_id = ?",
                                   (mid, link["person_id"])).fetchall():
                b.hard_delete("media_regions", rg["id"])       # untagging removes their boxes too
            b.touch(link["person_id"])
            if p:
                conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Untagged {_nm(p)} from a photo", b.id))
        b.hard_delete("media_links", link_id, op="unlink")
        item = media_out(conn, [conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()])[0]
        item.pop("_sort", None)
        return {"media": item, "batchId": b.id}


@router.delete("/people/{pid}/photo")
def remove_photo(pid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        person = require_person(conn, pid)
        if not person["photo_media_id"]:
            raise HTTPException(404, "This person has no photo.")
        b = Batch(conn, user["id"], f"Removed the profile photo of {_nm(person)}")
        b.update("people", pid, {"photo_media_id": None, "photo_region_id": None})   # the photo stays in their Photos tab
        b.touch(pid)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "batchId": b.id}


# ---------- the inbox and the Unsorted queue (§13.10) ----------
@router.post("/inbox/scan", dependencies=[Depends(features.required("inbox"))])
def scan_inbox(user: dict = Depends(require_user)):
    """Check now."""
    from .. import inbox
    media.require_online()
    res = inbox.scan_blocking(force=True)
    with db.get_conn() as conn:
        res["unsorted"] = conn.execute("SELECT COUNT(*) FROM media WHERE unsorted = 1 AND deleted_at IS NULL").fetchone()[0]
    res["folder"] = inbox.inbox_dir()
    return res


@router.get("/inbox/status", dependencies=[Depends(features.required("inbox"))])
def inbox_status(user: dict = Depends(require_user)):
    from .. import inbox
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM media WHERE unsorted = 1 AND deleted_at IS NULL").fetchone()[0]
    return {"enabled": True, "folder": inbox.inbox_dir(), "unsorted": n}


class IdsIn(Strict):
    ids: list[str] = Field(min_length=1, max_length=500)
    personId: str | None = None


@router.post("/media/sorted", dependencies=[Depends(features.required("inbox"))])
def mark_sorted(body: IdsIn, user: dict = Depends(require_user)):
    """Done: take items out of the Unsorted queue (one batch)."""
    with db.get_conn() as conn:
        b = Batch(conn, user["id"], f"Sorted {len(body.ids)} photo{'s' if len(body.ids) != 1 else ''}")
        for mid in body.ids:
            if conn.execute("SELECT 1 FROM media WHERE id = ? AND unsorted = 1", (mid,)).fetchone():
                b.update("media", mid, {"unsorted": 0})
        return {"ok": True, "batchId": b.id}


@router.post("/media/bulk-link", dependencies=[Depends(features.required("inbox"))])
def bulk_link(body: IdsIn, user: dict = Depends(require_user)):
    """Tag one person in several photos at once."""
    if not body.personId:
        raise HTTPException(422, "Choose who is in them.")
    with db.get_conn() as conn:
        who = require_person(conn, body.personId)
        b = Batch(conn, user["id"], f"Tagged {_nm(who)} in {len(body.ids)} photo{'s' if len(body.ids) != 1 else ''}")
        for mid in body.ids:
            if not conn.execute("SELECT 1 FROM media WHERE id = ? AND deleted_at IS NULL", (mid,)).fetchone():
                continue
            if not conn.execute("SELECT 1 FROM media_links WHERE media_id = ? AND person_id = ?", (mid, body.personId)).fetchone():
                _check_link_target(conn, person_id=body.personId)
                b.insert("media_links", {"id": db.new_id(), "media_id": mid, "person_id": body.personId}, op="link")
        b.touch(body.personId)
        return {"ok": True, "batchId": b.id}


# ---------- photo fixes (§13.19) ----------
class EditIn(Strict):
    edit: dict | None = None


@router.get("/media/{mid}/preview", dependencies=[Depends(features.required("photo_fixes"))])
def preview_edit(mid: str, edit: str = Query(..., max_length=500), user: dict = Depends(require_user)):
    """The fix recipe applied to the 1024 px copy, not saved — for the editor's preview."""
    from PIL import Image
    try:
        recipe = photoedit.clean(json.loads(edit))
    except (ValueError, photoedit.EditError) as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        row = conn.execute("SELECT kind FROM media WHERE id = ?", (mid,)).fetchone()
    if not row or row["kind"] != "photo" or not media.valid_id(mid):
        raise HTTPException(404, "That photo doesn't exist.")
    media.require_online()
    try:
        with Image.open(media.file_path(mid, "1024")) as src:
            src.load()
            img = photoedit.render(src.convert("RGB"), recipe)
    except OSError:
        raise HTTPException(404, "That photo's file is missing from the media folder.")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=80)
    return Response(content=out.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.put("/media/{mid}/edit", dependencies=[Depends(features.required("photo_fixes"))])
def edit_photo(mid: str, body: EditIn, user: dict = Depends(require_user)):
    """Save a fix recipe (rotate, straighten, crop, auto contrast), or null to
    revert to the original. The original file is never changed."""
    try:
        recipe = photoedit.clean(body.edit)
    except photoedit.EditError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if not row or row["deleted_at"]:
            raise HTTPException(404, "That photo doesn't exist.")
        if row["kind"] != "photo":
            raise HTTPException(422, "Only photos can be fixed.")
    if recipe:
        media.require_online()
        try:
            media.ensure_edited(mid, recipe)                  # render first: a bad file fails before anything is saved
        except OSError:
            raise HTTPException(404, "That photo's file is missing from the media folder.")
    with db.get_conn() as conn:
        b = Batch(conn, user["id"], "Fixed a photo" if recipe else "Put a photo back to the original")
        b.update("media", mid, {"edit": json.dumps(recipe, sort_keys=True) if recipe else None})
        for l in conn.execute("SELECT person_id FROM media_links WHERE media_id = ? AND person_id IS NOT NULL", (mid,)):
            b.touch(l["person_id"])
        return {"media": _media_item(conn, mid), "batchId": b.id}


# ---------- tag boxes on photos (§13.2) ----------
class RegionIn(Strict):
    personId: str | None = None
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0.01, le=1)
    h: float = Field(ge=0.01, le=1)


def _box(body, mrow=None) -> dict:
    """Clamp a box into the photo (rounding may push it a hair outside). Boxes
    arrive as drawn on the picture shown — on an edited photo that's moved back
    onto the original (§13.19)."""
    x, y = min(body.x, 0.99), min(body.y, 0.99)
    if mrow is not None and mrow["edit"] and mrow["width"] and mrow["height"]:
        o = photoedit.box_to_original({"x": x, "y": y, "w": body.w, "h": body.h}, _edit_of(mrow), mrow["width"], mrow["height"])
        x, y = min(max(o["x"], 0), 0.99), min(max(o["y"], 0), 0.99)
        body = type("B", (), {"w": o["w"], "h": o["h"]})
    w, h = min(body.w, 1 - x), min(body.h, 1 - y)
    if w < 0.01 or h < 0.01:
        raise HTTPException(422, "That box is too small — drag a bigger one around the face.")
    return {"x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5)}


def _photo_row(conn, mid):
    row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
    if not row or row["deleted_at"]:
        raise HTTPException(404, "That photo doesn't exist.")
    if row["kind"] != "photo":
        raise HTTPException(422, "Only photos can have people boxed on them.")
    return row


def _media_item(conn, mid):
    item = media_out(conn, [conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()])[0]
    item.pop("_sort", None)
    return item


def _ensure_link(b: Batch, mid: str, pid: str) -> None:
    if not b.conn.execute("SELECT 1 FROM media_links WHERE media_id = ? AND person_id = ?", (mid, pid)).fetchone():
        b.insert("media_links", {"id": db.new_id(), "media_id": mid, "person_id": pid}, op="link")


@router.post("/media/{mid}/regions", status_code=201, dependencies=[Depends(features.required("photo_tagging"))])
def add_region(mid: str, body: RegionIn, user: dict = Depends(require_user)):
    """Draw a box around someone: it also tags them on the photo."""
    if not body.personId:
        raise HTTPException(422, "Choose who is in the box.")
    with db.get_conn() as conn:
        mrow = _photo_row(conn, mid)
        person = require_person(conn, body.personId)
        n = conn.execute("SELECT COUNT(*) FROM media_regions WHERE media_id = ?", (mid,)).fetchone()[0]
        if n >= 100:
            raise HTTPException(422, "A photo can have at most 100 boxes.")
        b = Batch(conn, user["id"], f"Tagged {_nm(person)} in a photo")
        if not conn.execute("SELECT 1 FROM media_links WHERE media_id = ? AND person_id = ?", (mid, body.personId)).fetchone():
            _check_link_target(conn, person_id=body.personId)       # the per-person link limit
        _ensure_link(b, mid, body.personId)
        rid = db.new_id()
        b.insert("media_regions", dict(_box(body, mrow), id=rid, media_id=mid, person_id=body.personId,
                                       created_by=user["id"], created_at=b.now))
        b.touch(body.personId)
        return {"media": _media_item(conn, mid), "regionId": rid, "batchId": b.id}


@router.patch("/media/{mid}/regions/{rid}", dependencies=[Depends(features.required("photo_tagging"))])
def move_region(mid: str, rid: str, body: RegionIn, user: dict = Depends(require_user)):
    """Move or resize a box, or say it's someone else."""
    with db.get_conn() as conn:
        mrow = _photo_row(conn, mid)
        rg = conn.execute("SELECT * FROM media_regions WHERE id = ? AND media_id = ?", (rid, mid)).fetchone()
        if not rg:
            raise HTTPException(404, "That box isn't on this photo.")
        new_pid = body.personId or rg["person_id"]
        person = require_person(conn, new_pid)
        b = Batch(conn, user["id"], f"Moved {_nm(person)}'s box on a photo")
        fields = _box(body, mrow)
        if new_pid != rg["person_id"]:
            conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Tagged {_nm(person)} in a photo", b.id))
            _ensure_link(b, mid, new_pid)
            fields["person_id"] = new_pid
            old = conn.execute("SELECT id FROM people WHERE photo_region_id = ?", (rid,)).fetchone()
            if old:                                             # their profile crop was this box
                b.update("people", old["id"], {"photo_region_id": None})
                b.touch(old["id"])
        b.update("media_regions", rid, fields)
        b.touch(new_pid, rg["person_id"])
        return {"media": _media_item(conn, mid), "batchId": b.id}


@router.delete("/media/{mid}/regions/{rid}", dependencies=[Depends(features.required("photo_tagging"))])
def delete_region(mid: str, rid: str, user: dict = Depends(require_user)):
    """Remove a box. The person stays tagged; a profile photo cropped to it shows the whole photo."""
    with db.get_conn() as conn:
        rg = conn.execute("SELECT * FROM media_regions WHERE id = ? AND media_id = ?", (rid, mid)).fetchone()
        if not rg:
            raise HTTPException(404, "That box isn't on this photo.")
        who = conn.execute("SELECT * FROM people WHERE id = ?", (rg["person_id"],)).fetchone()
        b = Batch(conn, user["id"], f"Removed {_nm(who)}'s box from a photo" if who else "Removed a box from a photo")
        for p in conn.execute("SELECT id FROM people WHERE photo_region_id = ?", (rid,)).fetchall():
            b.update("people", p["id"], {"photo_region_id": None})
            b.touch(p["id"])
        b.hard_delete("media_regions", rid)
        b.touch(rg["person_id"])
        return {"media": _media_item(conn, mid), "batchId": b.id}


def _crop_bytes(path: str, box: dict, size: int) -> bytes:
    """The box, widened to a square around its centre (inside the photo), as a JPEG."""
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        W, H = im.size
        cx, cy = (box["x"] + box["w"] / 2) * W, (box["y"] + box["h"] / 2) * H
        side = max(box["w"] * W, box["h"] * H) * 1.15               # a little room around the face
        side = min(side, W, H)
        left = min(max(cx - side / 2, 0), W - side)
        top = min(max(cy - side / 2, 0), H - side)
        crop = im.crop((round(left), round(top), round(left + side), round(top + side)))
        crop.thumbnail((size, size))
        out = io.BytesIO()
        crop.save(out, "JPEG", quality=85)
        return out.getvalue()


@router.get("/media/{mid}/file")
def media_file(mid: str, size: str = Query("1024", pattern="^(256|1024|display|original)$"), region: str | None = None,
               user: dict = Depends(require_user)):
    if not media.valid_id(mid):
        raise HTTPException(404, "That photo doesn't exist.")
    with db.get_conn() as conn:
        row = conn.execute("SELECT content_type, kind, edit, width, height FROM media WHERE id = ?", (mid,)).fetchone()
    if not row:
        raise HTTPException(404, "That photo doesn't exist.")
    if not media.is_online():
        raise HTTPException(503, media.status()["reason"] or "Photo storage isn't reachable.")
    if row["kind"] == "document":
        size = "original"
    box = None
    if region and row["kind"] == "photo":
        with db.get_conn() as conn:
            rg = conn.execute("SELECT x, y, w, h FROM media_regions WHERE id = ? AND media_id = ?", (region, mid)).fetchone()
        box = _shown(rg, row) if rg else None                   # a stale box id just shows the whole photo
    edit = _edit_of(row) if row["kind"] == "photo" else None
    if size == "display" and row["kind"] == "document":
        size = "original"
    try:
        path = media.served_path(mid, "1024" if box and size == "256" else size, edit)
    except (OSError, ValueError):
        raise HTTPException(404, "That photo's file is missing from the media folder.")
    if not os.path.isfile(path):
        raise HTTPException(404, "That photo's file is missing from the media folder.")
    if box:
        data = _crop_bytes(path, box, 256 if size == "256" else 1024)
        return Response(content=data, media_type="image/jpeg", headers={
            "X-Content-Type-Options": "nosniff", "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=86400"})
    ctype = row["content_type"] if size == "original" or (size == "display" and not edit) else "image/jpeg"
    disp = "attachment" if ctype == "application/pdf" else "inline"
    return FileResponse(path, media_type=ctype, headers={
        "X-Content-Type-Options": "nosniff", "Content-Disposition": disp,
        "Cache-Control": "private, max-age=86400"})
