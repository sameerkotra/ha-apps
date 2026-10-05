"""Tree data in the website export, and importing it into another Family Tree (§13.6.3, v2.1.0).

Export: every website zip also holds `family-tree.json` — the same people, families, events, stories and
photos the pages show, built from the same filtered projection (`export_view.View`), so nothing that's
switched off for the website (living people's details, keep-out people, notes, places…) can leave through
it. "Private" placeholders aren't in it. Photos are the files already in the zip's media/ folder.

Import (Admin → Import, admins only) is incremental:
- Everything imported is remembered per source install (`import_links`: their id → our id), so importing a
  newer export of the same tree again only adds what's new.
- A person, family, event, story or photo that isn't linked yet is added. On first import, a person with the
  same name and birth year as exactly one person here is matched to them instead (the admin can untick it).
- Linked people only gain details: an empty field is filled, missing events, stories and photos are added,
  missing parent/partner/child links are made. Nothing that's already here is changed or overwritten.
- Something imported before from the same source that isn't in the new file may have been deleted there
  (or the export simply covered less). The preview lists it; the admin picks Remove or Keep for each. Kept
  ("ignored") ones aren't asked about again until they come back in a later file.
The whole import is one history batch, so History → Undo takes it back.
"""
import io
import json
import os
import re
import zipfile

from . import config, db, dates, media, names
from .models import FAMILY_EVENT_TYPES, FAMILY_KINDS, GENDERS, OTHER_NAME_TYPES, PERSON_EVENT_TYPES, RELATIONS

FORMAT = "family-tree-export"
VERSION = 1
DATA_NAME = "family-tree.json"
MAX_PEOPLE = 20000
MAX_ZIP_BYTES = 2 * 1024 * 1024 * 1024
MAX_JSON_BYTES = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENDED = ("divorced", "separated", "widowed")
_EVENT_COLS = ("type", "title", "date_text", "date_y", "date_m", "date_d", "date_approx", "sort_key", "place",
               "description", "time")


class ImportError_(ValueError):
    """A readable 422."""


def install_id(conn) -> str:
    """This install's id (made once) — tells a tree's exports apart from another tree's."""
    v = db.get_setting(conn, "install_id")
    if not v:
        v = db.new_id()
        db.set_setting(conn, "install_id", v)
    return v


# =====================================================================================================
# export
# =====================================================================================================
def _event_out(e: dict, dates_mode: str, places: bool, notes: bool) -> dict:
    out = {"id": e["id"], "type": e["type"], "title": e["title"], "place": e["place"] if places else None,
           "description": e["description"] if notes else None, "date": None, "time": None}
    if e.get("date_text") and dates_mode == "full":
        out["date"] = {k: e[k] for k in ("date_text", "date_y", "date_m", "date_d", "date_approx", "sort_key")}
        out["time"] = e.get("time")
    elif e.get("date_y") and dates_mode == "year":
        out["date"] = dates.from_parts({"qual": "exact", "y": e["date_y"]})
    return out


def export_data(conn, v, options, files: dict, title: str) -> dict:
    """The data file for a website export. `files`: media id → its file name in the zip's media/ folder."""
    from .export_view import EVENT_GROUPS
    from . import features
    on = features.states()
    allowed = {t for grp, yes in options.events.items() if yes and grp in EVENT_GROUPS for t in EVENT_GROUPS[grp]}
    if not options.deaths:
        allowed -= {"death", "burial"}
    if not options.familyDetails:
        allowed -= {"divorce"}
    if not on["ceremonies"]:
        allowed -= set(features.CEREMONY_TYPES)
    real = {pid: p for pid, p in v.people.items() if not p["placeholder"]}
    full = {pid for pid, p in real.items() if not p["limited"]}
    rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM people WHERE deleted_at IS NULL")}
    ev_person, ev_family = {}, {}
    for e in conn.execute("SELECT * FROM events"):
        e = dict(e)
        if e["type"] not in allowed:
            continue
        if e["person_id"] in full:
            ev_person.setdefault(e["person_id"], []).append(e)
        elif e["family_id"]:
            ev_family.setdefault(e["family_id"], []).append(e)
    stories = {}
    if options.stories:
        for s in conn.execute("SELECT id, person_id, title, body FROM stories ORDER BY created_at, rowid"):
            if s["person_id"] in full:
                stories.setdefault(s["person_id"], []).append({"id": s["id"], "title": s["title"], "body": s["body"]})
    people = []
    for pid, p in real.items():
        r = rows.get(pid, {})
        item = {"id": pid, "limited": bool(p["limited"]), "given_names": r.get("given_names"),
                "surname": r.get("surname"), "nickname": None, "birth_surname": None, "other_names": [],
                "gender": p["gender"], "deceased": bool(p["died"]), "biography": None, "events": [], "stories": [],
                "photo": None}
        if not p["limited"]:
            item["nickname"] = p["nickname"]
            item["birth_surname"] = p["birthSurname"]
            if options.otherNames and r.get("other_names"):
                item["other_names"] = [{"type": n.get("type", "aka"), "name": n.get("name")}
                                       for n in json.loads(r["other_names"]) or [] if n.get("name")]
            item["biography"] = p["biography"]
            item["events"] = [_event_out(e, options.dates, options.places, options.notes)
                              for e in ev_person.get(pid, [])]
            item["stories"] = stories.get(pid, [])
            item["photo"] = p.get("photo") if p.get("photo") in files else None
        people.append(item)
    private = {pid for pid, p in v.people.items() if p["limited"] or p["placeholder"]}
    families, shown_events = [], {e["id"] for evs in ev_person.values() for e in evs}
    for f in v.families:
        p1 = f["p1"] if f["p1"] in real else None
        p2 = f["p2"] if f["p2"] in real else None
        if not p1 and not p2:
            continue
        kids = [{"id": c["id"], "relation": c["relation"], "position": i}
                for i, c in enumerate(f["children"]) if c["id"] in real]
        hide = any(x and (x not in real or x in private) for x in (f["p1"], f["p2"]))
        evs = [] if hide else [_event_out(e, options.dates, options.places, options.notes) for e in ev_family.get(f["id"], [])]
        shown_events.update(e["id"] for e in evs)
        families.append({"id": f["id"], "p1": p1, "p2": p2, "kind": f["kind"], "ended": f["ended"], "children": kids,
                         "events": evs})
    fam_ids = {f["id"] for f in families}
    links = {}
    for l in conn.execute("SELECT media_id, person_id, family_id, event_id FROM media_links"):
        links.setdefault(l["media_id"], []).append(l)
    media_out = []
    for mid, m in v.media.items():
        if mid not in files:
            continue
        ls = links.get(mid, [])
        media_out.append({
            "id": mid, "kind": m["kind"], "title": m["title"], "description": m["description"],
            "file": f"media/{files[mid]}", "contentType": m["contentType"],
            "people": sorted({l["person_id"] for l in ls if l["person_id"] in full}),
            "families": sorted({l["family_id"] for l in ls if l["family_id"] in fam_ids}),
            "events": sorted({l["event_id"] for l in ls if l["event_id"] in shown_events}),
        })
    return {"format": FORMAT, "version": VERSION, "app": "Family Tree",
            "source": {"id": install_id(conn), "title": title, "exportedAt": config.now_iso()},
            "people": people, "families": families, "media": media_out}


# =====================================================================================================
# reading a file
# =====================================================================================================
def read_package(path: str) -> tuple[dict, str | None]:
    """→ (data, zip path or None). A website zip (with family-tree.json inside) or the JSON on its own."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(4)
    if head[:2] == b"PK":
        if size > MAX_ZIP_BYTES:
            raise ImportError_("That file is too large to import.")
        try:
            with zipfile.ZipFile(path) as z:
                info = next((i for i in z.infolist() if i.filename.rsplit("/", 1)[-1] == DATA_NAME), None)
                if info is None:
                    raise ImportError_("This zip has no family-tree.json. Export the website again from Family Tree "
                                       "2.1 or later (Export → Website) and import that zip.")
                if info.file_size > MAX_JSON_BYTES:
                    raise ImportError_("The tree data in that zip is too large.")
                raw = z.read(info)
        except zipfile.BadZipFile:
            raise ImportError_("That isn't a readable zip file.")
        zpath = path
    else:
        if size > MAX_JSON_BYTES:
            raise ImportError_("That file is too large to import.")
        with open(path, "rb") as f:
            raw = f.read()
        zpath = None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ImportError_("The tree data couldn't be read (it isn't valid JSON).")
    return validate(data), zpath


def _s(v, limit):
    if v is None:
        return None
    if not isinstance(v, str):
        raise ImportError_("The tree data has a value of the wrong type.")
    v = v.strip()
    return v[:limit] or None


def _int(v, lo, hi):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ImportError_("The tree data has a number out of range.")
    return v


def _id(v):
    if not isinstance(v, str) or not _ID_RE.match(v):
        raise ImportError_("The tree data has an invalid id.")
    return v


def _event_in(e, allowed) -> dict | None:
    if not isinstance(e, dict):
        raise ImportError_("The tree data has a broken event.")
    t = e.get("type")
    if t not in allowed:
        return None                                   # a type this install doesn't know: skipped
    out = {"id": _id(e.get("id")), "type": t, "title": _s(e.get("title"), 200), "place": _s(e.get("place"), 300),
           "description": _s(e.get("description"), 5000), "time": None, "date": None}
    d = e.get("date")
    if d:
        if not isinstance(d, dict):
            raise ImportError_("The tree data has a broken date.")
        text = _s(d.get("date_text"), 80)
        if text:
            try:
                cols = dates.parse_text(text)
            except dates.DateError:
                cols = None
            out["date"] = cols
    if out["date"] and isinstance(e.get("time"), str) and re.match(r"^([01][0-9]|2[0-3]):[0-5][0-9]$", e["time"]):
        out["time"] = e["time"] if t in ("birth", "death") else None
    return out


def validate(data) -> dict:
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ImportError_("That isn't a Family Tree export.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ImportError_("That export was made by a newer Family Tree — update this app first.")
    src = data.get("source") or {}
    out = {"source": {"id": _id(src.get("id")), "title": _s(src.get("title"), 120) or "Family Tree",
                      "exportedAt": _s(src.get("exportedAt"), 40)}, "people": [], "families": [], "media": []}
    people = data.get("people") or []
    if not isinstance(people, list) or len(people) > MAX_PEOPLE:
        raise ImportError_(f"An import can have at most {MAX_PEOPLE} people.")
    seen = set()
    for p in people:
        if not isinstance(p, dict):
            raise ImportError_("The tree data has a broken person.")
        pid = _id(p.get("id"))
        if pid in seen:
            raise ImportError_("The tree data lists someone twice.")
        seen.add(pid)
        g = p.get("gender") if p.get("gender") in GENDERS else "unknown"
        other = []
        for n in (p.get("other_names") or [])[:10]:
            if isinstance(n, dict) and _s(n.get("name"), 100):
                other.append({"type": n.get("type") if n.get("type") in OTHER_NAME_TYPES else "aka",
                              "name": _s(n.get("name"), 100)})
        item = {"id": pid, "limited": bool(p.get("limited")), "given_names": _s(p.get("given_names"), 100),
                "surname": _s(p.get("surname"), 100), "nickname": _s(p.get("nickname"), 100),
                "birth_surname": _s(p.get("birth_surname"), 100), "other_names": other, "gender": g,
                "deceased": bool(p.get("deceased")), "biography": _s(p.get("biography"), 20000),
                "events": [x for x in (_event_in(e, PERSON_EVENT_TYPES) for e in (p.get("events") or [])[:500]) if x],
                "stories": [], "photo": p.get("photo") if isinstance(p.get("photo"), str) and _ID_RE.match(p["photo"]) else None}
        for s in (p.get("stories") or [])[:200]:
            if isinstance(s, dict) and _s(s.get("body"), 50000):
                item["stories"].append({"id": _id(s.get("id")), "title": _s(s.get("title"), 200) or "Story",
                                        "body": _s(s.get("body"), 50000)})
        if not any(item[k] for k in ("given_names", "surname", "nickname")):
            item["given_names"] = "Unknown"
        out["people"].append(item)
    for f in data.get("families") or []:
        if not isinstance(f, dict):
            raise ImportError_("The tree data has a broken family.")
        p1 = f.get("p1") if f.get("p1") in seen else None
        p2 = f.get("p2") if f.get("p2") in seen else None
        kids = []
        for c in (f.get("children") or [])[:200]:
            if isinstance(c, dict) and c.get("id") in seen:
                kids.append({"id": c["id"], "relation": c.get("relation") if c.get("relation") in RELATIONS else "birth"})
        if not p1 and not p2:
            continue
        out["families"].append({"id": _id(f.get("id")), "p1": p1, "p2": p2,
                                "kind": f.get("kind") if f.get("kind") in FAMILY_KINDS else "unknown",
                                "ended": f.get("ended") if f.get("ended") in ENDED else None, "children": kids,
                                "events": [x for x in (_event_in(e, FAMILY_EVENT_TYPES) for e in (f.get("events") or [])[:100]) if x]})
    for m in data.get("media") or []:
        if not isinstance(m, dict) or m.get("kind") not in ("photo", "document"):
            continue
        fname = m.get("file")
        if not isinstance(fname, str) or not re.match(r"^media/[A-Za-z0-9_.-]{1,100}$", fname):
            continue
        out["media"].append({"id": _id(m.get("id")), "kind": m["kind"], "title": _s(m.get("title"), 200),
                             "description": _s(m.get("description"), 5000), "file": fname,
                             "people": [x for x in m.get("people") or [] if x in seen],
                             "families": [x for x in m.get("families") or [] if isinstance(x, str)],
                             "events": [x for x in m.get("events") or [] if isinstance(x, str)]})
    return out


# =====================================================================================================
# planning and applying
# =====================================================================================================
def _norm(s) -> str:
    return " ".join((s or "").lower().split())


def _link(conn, src, kind, rid):
    r = conn.execute("SELECT local_id, ignored_missing FROM import_links WHERE source_id = ? AND kind = ? AND remote_id = ?",
                     (src, kind, rid)).fetchone()
    return r["local_id"] if r else None


def _alive_local(conn, kind, lid) -> bool:
    q = {"person": "SELECT 1 FROM people WHERE id = ? AND deleted_at IS NULL AND merged_into IS NULL",
         "family": "SELECT 1 FROM families WHERE id = ? AND deleted_at IS NULL",
         "event": "SELECT 1 FROM events WHERE id = ?", "story": "SELECT 1 FROM stories WHERE id = ?",
         "media": "SELECT 1 FROM media WHERE id = ? AND deleted_at IS NULL",
         "child": "SELECT 1 FROM family_children WHERE family_id || '|' || child_id = ?"}[kind]
    return conn.execute(q, (lid,)).fetchone() is not None


def _person_label(p) -> str:
    return names.full(p.get("given_names"), p.get("surname"), p.get("nickname"), None) or "Someone"


def plan(conn, data: dict, unmatch: set | None = None) -> dict:
    """What an import would do, without writing anything. `unmatch`: remote ids the admin said aren't the
    person they were matched to (they're added as new people instead)."""
    unmatch = unmatch or set()
    src = data["source"]["id"]
    if src == install_id(conn):
        raise ImportError_("That export came from this same Family Tree — there's nothing to import.")
    local = {r["id"]: dict(r) for r in conn.execute(
        "SELECT * FROM people WHERE deleted_at IS NULL AND merged_into IS NULL")}
    births = {r["person_id"]: r["date_y"] for r in conn.execute("SELECT person_id, date_y FROM events WHERE type = 'birth' AND person_id IS NOT NULL")}
    by_key = {}
    for pid, r in local.items():
        key = (_norm(r["given_names"]), _norm(r["surname"]), births.get(pid))
        by_key.setdefault(key, []).append(pid)
    linked_ids = {r["local_id"] for r in conn.execute("SELECT local_id FROM import_links WHERE kind = 'person'")}
    pmap, new_people, matched, updated = {}, [], [], []
    for p in data["people"]:
        lid = _link(conn, src, "person", p["id"])
        if lid and lid in local:
            pmap[p["id"]] = lid
            adds = _person_additions(conn, local[lid], p, src)
            if adds:
                updated.append({"remoteId": p["id"], "localId": lid, "name": _person_label(local[lid]), "adds": adds})
            continue
        birth_y = next((e["date"]["date_y"] for e in p["events"] if e["type"] == "birth" and e["date"]), None)
        cands = by_key.get((_norm(p["given_names"]), _norm(p["surname"]), birth_y), []) if birth_y else []
        cands = [c for c in cands if c not in linked_ids]
        if len(cands) == 1 and p["id"] not in unmatch:
            pmap[p["id"]] = cands[0]
            matched.append({"remoteId": p["id"], "localId": cands[0], "name": _person_label(p), "born": birth_y,
                            "adds": _person_additions(conn, local[cands[0]], p, src)})
        else:
            new_people.append({"remoteId": p["id"], "name": _person_label(p), "born": birth_y,
                               "candidate": bool(cands)})
    # families
    fam_new, fam_children = [], []
    local_fams = [dict(r) for r in conn.execute("SELECT * FROM families WHERE deleted_at IS NULL")]
    for f in data["families"]:
        lid = _link(conn, src, "family", f["id"])
        if lid and not _alive_local(conn, "family", lid):
            lid = None
        if not lid:
            lid = _match_family(local_fams, pmap.get(f["p1"]), pmap.get(f["p2"]))
        names_ = " & ".join(_person_label(next(p for p in data["people"] if p["id"] == x)) for x in (f["p1"], f["p2"]) if x)
        if not lid:
            fam_new.append({"remoteId": f["id"], "name": names_ or "A family", "children": len(f["children"])})
        else:
            have = {r["child_id"] for r in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (lid,))}
            missing = [c for c in f["children"] if pmap.get(c["id"]) not in have]
            if missing:
                fam_children.append({"remoteId": f["id"], "name": names_, "children": len(missing)})
    deletions = _deletions(conn, data, src)
    n_media = sum(1 for m in data["media"] if not (_link(conn, src, "media", m["id"]) and
                                                   _alive_local(conn, "media", _link(conn, src, "media", m["id"]))))
    known = conn.execute("SELECT * FROM import_sources WHERE id = ?", (src,)).fetchone()
    return {"source": data["source"], "firstImport": known is None,
            "lastImportAt": known["last_import_at"] if known else None,
            "counts": {"people": len(data["people"]), "newPeople": len(new_people), "matched": len(matched),
                       "updated": len(updated), "newFamilies": len(fam_new), "familiesGainingChildren": len(fam_children),
                       "newMedia": n_media, "deletions": len(deletions)},
            "newPeople": new_people[:500], "matched": matched, "updated": updated[:500], "newFamilies": fam_new[:500],
            "familiesGainingChildren": fam_children[:500], "deletions": deletions,
            "mediaOnline": media.is_online(), "_pmap": pmap}


def _person_additions(conn, row, p, src) -> list:
    """Human-readable list of what a linked person would gain (empty fields filled, new events/stories/photos)."""
    out = []
    for col, label in (("given_names", "first name"), ("surname", "surname"), ("nickname", "nickname"),
                       ("birth_surname", "birth surname"), ("biography", "biography")):
        if p.get(col) and not row.get(col):
            out.append(label)
    if p["gender"] != "unknown" and row.get("gender") == "unknown":
        out.append("gender")
    if p["deceased"] and not row.get("deceased"):
        out.append("marked as died")
    if p["other_names"] and not row.get("other_names"):
        out.append("other names")
    for e in p["events"]:
        lid = _link(conn, src, "event", e["id"])
        if lid and _alive_local(conn, "event", lid):
            continue
        if _event_exists(conn, "person_id", row["id"], e):
            continue
        out.append(e["type"].replace("_", " "))
    for s in p["stories"]:
        lid = _link(conn, src, "story", s["id"])
        if not (lid and _alive_local(conn, "story", lid)) and not conn.execute(
                "SELECT 1 FROM stories WHERE person_id = ? AND body = ?", (row["id"], s["body"])).fetchone():
            out.append("a story")
    return out


def _event_exists(conn, col, owner, e) -> bool:
    if e["type"] in ("birth", "death", "marriage"):
        return conn.execute(f"SELECT 1 FROM events WHERE {col} = ? AND type = ?", (owner, e["type"])).fetchone() is not None
    dt = e["date"]["date_text"] if e["date"] else None
    return conn.execute(f"SELECT 1 FROM events WHERE {col} = ? AND type = ? AND COALESCE(date_text,'') = ? "
                        "AND COALESCE(place,'') = ? AND COALESCE(title,'') = ?",
                        (owner, e["type"], dt or "", e["place"] or "", e["title"] or "")).fetchone() is not None


def _match_family(local_fams, a, b):
    if not a and not b:
        return None
    want = {x for x in (a, b) if x}
    for f in local_fams:
        have = {x for x in (f["partner1_id"], f["partner2_id"]) if x}
        if have == want:
            return f["id"]
    return None


def _deletions(conn, data, src) -> list:
    """Linked items from this source that aren't in the file and still exist here (and weren't kept before)."""
    present = {"person": {p["id"] for p in data["people"]}, "family": {f["id"] for f in data["families"]},
               "event": {e["id"] for p in data["people"] for e in p["events"]} | {e["id"] for f in data["families"] for e in f["events"]},
               "story": {s["id"] for p in data["people"] for s in p["stories"]}, "media": {m["id"] for m in data["media"]},
               "child": {f"{f['id']}|{c['id']}" for f in data["families"] for c in f["children"]}}
    out = []
    for r in conn.execute("SELECT * FROM import_links WHERE source_id = ? ORDER BY kind, remote_id", (src,)):
        if r["remote_id"] in present[r["kind"]]:
            continue
        if r["ignored_missing"] or not _alive_local(conn, r["kind"], r["local_id"]):
            continue
        out.append({"key": f"{r['kind']}:{r['remote_id']}", "kind": r["kind"], "label": _describe(conn, r["kind"], r["local_id"])})
    return out


def _describe(conn, kind, lid) -> str:
    if kind == "person":
        r = conn.execute("SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?", (lid,)).fetchone()
        return names.row_name(r)
    if kind == "family":
        r = conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (lid,)).fetchone()
        ps = [names.row_name(conn.execute("SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?", (x,)).fetchone())
              for x in (r["partner1_id"], r["partner2_id"]) if x]
        return "The family of " + " & ".join(ps) if ps else "A family"
    if kind == "child":
        fid, cid = lid.split("|")
        c = conn.execute("SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?", (cid,)).fetchone()
        return f"{names.row_name(c)} as a child in a family" if c else "A child link"
    if kind == "event":
        r = conn.execute("SELECT type, date_text, person_id, family_id FROM events WHERE id = ?", (lid,)).fetchone()
        who = ""
        if r["person_id"]:
            pr = conn.execute("SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?", (r["person_id"],)).fetchone()
            who = f" of {names.row_name(pr)}" if pr else ""
        return f"{r['type'].replace('_', ' ').capitalize()}{who}{' (' + r['date_text'] + ')' if r['date_text'] else ''}"
    if kind == "story":
        r = conn.execute("SELECT title FROM stories WHERE id = ?", (lid,)).fetchone()
        return f"Story “{r['title']}”"
    r = conn.execute("SELECT title, kind FROM media WHERE id = ?", (lid,)).fetchone()
    return f"{'Photo' if r['kind'] == 'photo' else 'Document'}{' “' + r['title'] + '”' if r['title'] else ''}"


def _remember(conn, src, kind, rid, lid) -> None:
    conn.execute("INSERT INTO import_links (source_id, kind, remote_id, local_id, ignored_missing) VALUES (?, ?, ?, ?, 0) "
                 "ON CONFLICT(source_id, kind, remote_id) DO UPDATE SET local_id = excluded.local_id, ignored_missing = 0",
                 (src, kind, rid, lid))


def apply(conn, user_id: str, data: dict, zpath: str | None, unmatch: set, remove: set) -> dict:
    """Do the import in one history batch. `remove`: deletion keys ("kind:remoteId") the admin chose to
    remove; every other listed deletion is kept and not asked about again."""
    from .history import Batch
    from . import graph as graph_mod
    p = plan(conn, data, unmatch)
    src = data["source"]["id"]
    b = Batch(conn, user_id, f"Imported from {data['source']['title']}"[:200])
    now = b.now
    pmap = dict(p["_pmap"])                  # linked and matched people; everyone else is new
    stats = {"people": 0, "details": 0, "families": 0, "children": 0, "events": 0, "stories": 0, "media": 0,
             "removed": 0, "skipped": []}
    # ---- people ----
    for rp in data["people"]:
        lid = pmap.get(rp["id"])
        if not lid:
            lid = db.new_id()
            b.insert("people", {"id": lid, "given_names": rp["given_names"], "surname": rp["surname"],
                                "nickname": rp["nickname"], "birth_surname": rp["birth_surname"],
                                "other_names": json.dumps(rp["other_names"]) if rp["other_names"] else None,
                                "gender": rp["gender"], "deceased": 1 if rp["deceased"] else 0, "biography": rp["biography"],
                                "remind": 0, "created_by": user_id, "created_at": now, "updated_at": now})
            pmap[rp["id"]] = lid
            stats["people"] += 1
        else:
            row = dict(conn.execute("SELECT * FROM people WHERE id = ?", (lid,)).fetchone())
            fill = {c: rp[c] for c in ("given_names", "surname", "nickname", "birth_surname", "biography") if rp.get(c) and not row.get(c)}
            if rp["gender"] != "unknown" and row["gender"] == "unknown":
                fill["gender"] = rp["gender"]
            if rp["deceased"] and not row["deceased"]:
                fill["deceased"] = 1
            if rp["other_names"] and not row["other_names"]:
                fill["other_names"] = json.dumps(rp["other_names"])
            if fill:
                b.update("people", lid, fill)
                stats["details"] += 1
        _remember(conn, src, "person", rp["id"], lid)
        b.touch(lid)
        for e in rp["events"]:
            stats["events"] += _import_event(conn, b, src, e, "person_id", lid)
        for s in rp["stories"]:
            sl = _link(conn, src, "story", s["id"])
            if sl and _alive_local(conn, "story", sl):
                continue
            if conn.execute("SELECT 1 FROM stories WHERE person_id = ? AND body = ?", (lid, s["body"])).fetchone():
                continue
            sid = db.new_id()
            b.insert("stories", {"id": sid, "person_id": lid, "title": s["title"], "body": s["body"],
                                 "created_by": user_id, "created_at": now, "updated_at": now})
            _remember(conn, src, "story", s["id"], sid)
            stats["stories"] += 1
    # ---- families ----
    fmap = {}
    local_fams = [dict(r) for r in conn.execute("SELECT * FROM families WHERE deleted_at IS NULL")]
    for f in data["families"]:
        a, c2 = pmap.get(f["p1"]), pmap.get(f["p2"])
        lid = _link(conn, src, "family", f["id"])
        if lid and not _alive_local(conn, "family", lid):
            lid = None
        if not lid:
            lid = _match_family(local_fams, a, c2)
        if not lid:
            lid = db.new_id()
            b.insert("families", {"id": lid, "partner1_id": a, "partner2_id": c2, "kind": f["kind"], "ended": f["ended"],
                                  "created_at": now, "updated_at": now})
            local_fams.append({"id": lid, "partner1_id": a, "partner2_id": c2})
            stats["families"] += 1
        else:
            row = dict(conn.execute("SELECT * FROM families WHERE id = ?", (lid,)).fetchone())
            fill = {}
            if f["kind"] != "unknown" and row["kind"] == "unknown":
                fill["kind"] = f["kind"]
            if f["ended"] and not row["ended"]:
                fill["ended"] = f["ended"]
            if (a or c2) and (row["partner1_id"] is None or row["partner2_id"] is None):
                have = {row["partner1_id"], row["partner2_id"]}
                extra = [x for x in (a, c2) if x and x not in have]
                if extra:
                    fill["partner1_id" if row["partner1_id"] is None else "partner2_id"] = extra[0]
            if fill:
                b.update("families", lid, fill)
        fmap[f["id"]] = lid
        _remember(conn, src, "family", f["id"], lid)
        b.touch(a, c2)
        for e in f["events"]:
            stats["events"] += _import_event(conn, b, src, e, "family_id", lid)
    # children (after every family exists); cycles are refused and reported
    g = graph_mod.load_fresh(conn)
    for f in data["families"]:
        lid = fmap[f["id"]]
        fam = conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (lid,)).fetchone()
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM family_children WHERE family_id = ?", (lid,)).fetchone()[0]
        for c in f["children"]:
            cid = pmap.get(c["id"])
            if not cid:
                continue
            key = f"{lid}|{cid}"
            if conn.execute("SELECT 1 FROM family_children WHERE family_id = ? AND child_id = ?", (lid, cid)).fetchone():
                _remember(conn, src, "child", f"{f['id']}|{c['id']}", key)
                continue
            if any(par and par in g.people and cid in g.people and g.is_descendant(par, cid)
                   for par in (fam["partner1_id"], fam["partner2_id"])) or cid in (fam["partner1_id"], fam["partner2_id"]):
                stats["skipped"].append(f"{names.row_name(conn.execute('SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?', (cid,)).fetchone())} "
                                        "wasn't added as a child: it would make someone their own ancestor.")
                continue
            b.insert("family_children", {"family_id": lid, "child_id": cid, "position": pos, "relation": c["relation"]}, op="link")
            pos += 1
            _remember(conn, src, "child", f"{f['id']}|{c['id']}", key)
            b.touch(cid, fam["partner1_id"], fam["partner2_id"])
            stats["children"] += 1
            g = graph_mod.load_fresh(conn)
    # ---- photos and documents ----
    if data["media"]:
        if not zpath:
            stats["skipped"].append("Photos weren't imported: import the website zip, not the data file on its own.")
        elif not media.is_online():
            stats["skipped"].append("Photos weren't imported: photo storage isn't reachable right now. Import the same "
                                    "zip again later to add them.")
        else:
            stats["media"] = _import_media(conn, b, src, data, zpath, pmap, fmap, user_id, stats)
    # ---- deletions the admin chose ----
    wanted = {d["key"] for d in p["deletions"]}
    for d in p["deletions"]:
        kind, rid = d["key"].split(":", 1)
        lid = _link(conn, src, kind, rid)
        if d["key"] in remove:
            _remove_local(b, kind, lid)
            conn.execute("DELETE FROM import_links WHERE source_id = ? AND kind = ? AND remote_id = ?", (src, kind, rid))
            stats["removed"] += 1
        else:
            conn.execute("UPDATE import_links SET ignored_missing = 1 WHERE source_id = ? AND kind = ? AND remote_id = ?",
                         (src, kind, rid))
    unknown = remove - wanted
    if unknown:
        stats["skipped"].append(f"{len(unknown)} removal(s) no longer applied and were skipped.")
    conn.execute("INSERT INTO import_sources (id, title, last_import_at, imports) VALUES (?, ?, ?, 1) "
                 "ON CONFLICT(id) DO UPDATE SET title = excluded.title, last_import_at = excluded.last_import_at, "
                 "imports = imports + 1", (src, data["source"]["title"], now))
    label = (f"Imported from {data['source']['title']}: {stats['people']} new "
             f"{'person' if stats['people'] == 1 else 'people'}, {stats['families']} new "
             f"{'family' if stats['families'] == 1 else 'families'}")
    conn.execute("UPDATE batches SET label = ? WHERE id = ?", (label[:200], b.id))
    stats["batchId"] = b.id
    return stats


def _import_event(conn, b, src, e, col, owner) -> int:
    lid = _link(conn, src, "event", e["id"])
    if lid and _alive_local(conn, "event", lid):
        return 0
    if _event_exists(conn, col, owner, e):
        ex = conn.execute(f"SELECT id FROM events WHERE {col} = ? AND type = ? ORDER BY created_at LIMIT 1", (owner, e["type"])).fetchone()
        if ex and e["type"] in ("birth", "death", "marriage"):
            # the one birth/death/marriage here: fill an empty date or place, never change one
            row = dict(conn.execute("SELECT * FROM events WHERE id = ?", (ex["id"],)).fetchone())
            fill = {}
            if e["date"] and not row["date_text"]:
                fill.update(e["date"])
            if e["place"] and not row["place"]:
                fill["place"] = e["place"]
            if e["time"] and not row.get("time") and (row["date_text"] or e["date"]):
                fill["time"] = e["time"]
            if fill:
                b.update("events", ex["id"], fill)
            _remember(conn, src, "event", e["id"], ex["id"])
        return 0
    cols = e["date"] or {"date_text": None, "date_y": None, "date_m": None, "date_d": None, "date_approx": 0, "sort_key": None}
    eid = db.new_id()
    b.insert("events", dict(cols, id=eid, **{col: owner}, type=e["type"], title=e["title"], place=e["place"],
                            description=e["description"], time=e["time"], created_at=b.now, updated_at=b.now))
    _remember(conn, src, "event", e["id"], eid)
    return 1


def _import_media(conn, b, src, data, zpath, pmap, fmap, user_id, stats) -> int:
    from .routers.media import MAX_LINKS_PER_PERSON
    n = 0
    ev_map = {r["remote_id"]: r["local_id"] for r in conn.execute(
        "SELECT remote_id, local_id FROM import_links WHERE source_id = ? AND kind = 'event'", (src,))}
    with zipfile.ZipFile(zpath) as z:
        names_in = set(z.namelist())
        for m in data["media"]:
            lid = _link(conn, src, "media", m["id"])
            if not (lid and _alive_local(conn, "media", lid)):
                if m["file"] not in names_in:
                    continue
                raw = z.read(m["file"])
                try:
                    ctype = media.validate_upload(raw, allow_documents=True)
                    if ctype == "application/pdf":
                        processed = {"content_type": ctype, "original": raw, "thumbs": {}, "width": None, "height": None,
                                     "date_text": None, "document": True}
                    else:
                        processed = media.process_image(raw)
                except media.MediaError as e:
                    stats["skipped"].append(f"A photo wasn't imported: {e}")
                    continue
                lid = db.new_id()
                if processed.get("document"):
                    media.store_document(lid, processed["original"])
                else:
                    media.store_image(lid, processed)
                b.insert("media", {"id": lid, "kind": "document" if processed.get("document") else "photo",
                                   "title": m["title"], "description": m["description"], "date_text": processed["date_text"],
                                   "content_type": processed["content_type"], "size": len(processed["original"]),
                                   "sha256": media.sha256(raw), "width": processed["width"], "height": processed["height"],
                                   "created_by": user_id, "created_at": b.now})
                n += 1
            _remember(conn, src, "media", m["id"], lid)
            have = {(r["person_id"], r["family_id"], r["event_id"]) for r in conn.execute(
                "SELECT person_id, family_id, event_id FROM media_links WHERE media_id = ?", (lid,))}
            targets = ([(pmap.get(x), None, None) for x in m["people"]] + [(None, fmap.get(x), None) for x in m["families"]]
                       + [(None, None, ev_map.get(x)) for x in m["events"]])
            for t in targets:
                if not any(t) or t in have:
                    continue
                if t[0] and conn.execute("SELECT COUNT(*) FROM media_links WHERE person_id = ?", (t[0],)).fetchone()[0] >= MAX_LINKS_PER_PERSON:
                    continue
                if t[2] and not conn.execute("SELECT 1 FROM events WHERE id = ?", (t[2],)).fetchone():
                    continue
                b.insert("media_links", {"id": db.new_id(), "media_id": lid, "person_id": t[0], "family_id": t[1], "event_id": t[2]},
                         op="link")
                have.add(t)
    # profile photos: only for someone who has none here
    for rp in data["people"]:
        if not rp["photo"]:
            continue
        mid = _link(conn, src, "media", rp["photo"])
        lid = pmap.get(rp["id"])
        if not mid or not lid:
            continue
        row = conn.execute("SELECT photo_media_id FROM people WHERE id = ?", (lid,)).fetchone()
        linked = conn.execute("SELECT 1 FROM media_links WHERE media_id = ? AND person_id = ?", (mid, lid)).fetchone()
        if row and not row["photo_media_id"] and linked:
            b.update("people", lid, {"photo_media_id": mid})
    return n


def _remove_local(b, kind, lid) -> None:
    if kind == "person":
        b.soft_delete("people", lid)
    elif kind == "family":
        b.soft_delete("families", lid)
    elif kind == "media":
        b.soft_delete("media", lid)
    elif kind == "event":
        for r in b.conn.execute("SELECT id FROM media_links WHERE event_id = ?", (lid,)).fetchall():
            b.hard_delete("media_links", r["id"], op="unlink")
        for r in b.conn.execute("SELECT id FROM citations WHERE event_id = ?", (lid,)).fetchall():
            b.hard_delete("citations", r["id"])
        b.conn.execute("DELETE FROM tithi_dates WHERE event_id = ?", (lid,))
        if b.conn.execute("SELECT 1 FROM event_tithi WHERE event_id = ?", (lid,)).fetchone():
            b.hard_delete("event_tithi", lid)
        b.hard_delete("events", lid)
    elif kind == "story":
        b.hard_delete("stories", lid)
    elif kind == "child":
        b.hard_delete("family_children", lid, op="unlink")
