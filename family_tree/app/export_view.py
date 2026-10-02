"""Export options and the one filtered projection every exporter reads (§13.6.1).

`build(conn, options, me_id)` applies, in this order:
  1. the scope (whole tree, ancestors/descendants of someone, a branch with
     stop points, or hand-picked people);
  2. people marked "keep out of all exports", who are dropped, or shown as a
     "Private" placeholder when they're the only link between people who stay;
  3. the living-people rule (limited = name and relationships only);
  4. the detail choices (names, dates, places, event groups, photos, text).
No exporter filters anything on its own, so a detail that's switched off here
can't leak out through one format.
"""
import json
import os
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import dates, graph as graph_mod, media, relations

EVENT_GROUPS = {
    "birth_death": ("birth", "death", "burial"),
    "marriage": ("marriage", "engagement", "divorce"),
    "education_occupation": ("education", "occupation", "retirement"),
    "residence": ("residence",),
    "migration": ("immigration", "emigration"),
    "military": ("military",),
    "religious": ("baptism", "religion", "namakaranam", "annaprasana", "aksharabhyasam", "upanayanam", "seemantham",
                  "shashtipoorthi", "sahasra_chandra", "ceremony", "nischitartham", "gruhapravesham"),   # & ceremonies
    "other": ("custom",),
}
EVENT_LABELS = {"birth": "Born", "death": "Died", "burial": "Buried", "baptism": "Baptism / naming",
                "education": "Education", "occupation": "Occupation", "residence": "Lived", "immigration": "Immigrated",
                "emigration": "Emigrated", "military": "Military service", "religion": "Religious event",
                "retirement": "Retired", "custom": "Event", "marriage": "Married", "engagement": "Engaged",
                "divorce": "Divorced"}
from .ceremonies import ALL as _CER   # noqa: E402
EVENT_LABELS.update({k: v["en"] for k, v in _CER.items()})
MAX_RELATIONSHIP_LABELS = 3000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeaveOut(_Strict):
    id: str
    keepChain: bool = False


class ScopeIn(_Strict):
    type: Literal["whole", "ancestors", "descendants", "both", "branch", "selected"] = "whole"
    personId: str | None = None                       # ancestors / descendants / both
    generations: int = Field(4, ge=1, le=30)
    partners: bool = True                             # also include partners of included people
    start: list[str] = Field(default_factory=list, max_length=20)          # branch
    walk: Literal["descendants", "ancestors", "both", "connected"] = "connected"
    partnerRule: Literal["stop", "follow", "exclude"] = "stop"
    stops: list[str] = Field(default_factory=list, max_length=500)
    leaveOut: list[LeaveOut] = Field(default_factory=list, max_length=500)
    ids: list[str] = Field(default_factory=list, max_length=20000)          # selected


class SitePages(_Strict):
    tree: bool = True
    people: bool = True
    surnames: bool = True
    places: bool = True


class SiteIn(_Strict):
    title: str = Field("Our family", max_length=120)
    intro: str = Field("", max_length=5000)
    homePersonId: str | None = None
    coverMediaId: str | None = None
    theme: Literal["heritage", "slate", "daylight", "parchment", "auto"] = "auto"
    pages: SitePages = Field(default_factory=SitePages)


def _default_events():
    return {k: k != "other" for k in EVENT_GROUPS}


class ExportOptions(_Strict):
    scope: ScopeIn = Field(default_factory=ScopeIn)
    living: Literal["limited", "full", "exclude"] = "limited"
    maidenNames: bool = True
    otherNames: bool = True
    dates: Literal["full", "year", "none"] = "full"
    places: bool = True
    events: dict[str, bool] = Field(default_factory=_default_events)
    photos: Literal["none", "profile", "all"] = "all"
    photoSize: Literal["web", "original"] = "web"
    documents: bool = False
    stories: bool = True            # biography and stories
    notes: bool = False             # event descriptions and photo notes
    relationships: bool = True
    relativeTo: str | None = None   # defaults to "This is me"
    gender: bool = True             # off: everyone "unknown" (neutral colours, "parent" not "mother")
    deaths: bool = True             # off: nobody is marked as died; no death or burial details
    familyDetails: bool = True      # off: no married/partners, divorced/separated, adopted/step/foster
    customFields: dict[str, bool] = Field(default_factory=dict)   # field id → on; missing = the field's own default (§13.8)
    contacts: bool = False          # phone numbers and addresses of the living (§13.11); off unless chosen
    sources: bool = False           # the sources behind the facts (§13.9)
    importData: bool = True         # family-tree.json in the zip, so another Family Tree can import it (§13.6.3)
    site: SiteIn = Field(default_factory=SiteIn)


@dataclass
class View:
    people: dict = field(default_factory=dict)       # id → person dict (placeholders included)
    families: list = field(default_factory=list)
    media: dict = field(default_factory=dict)        # id → media dict
    warnings: list = field(default_factory=list)
    relative_to: str | None = None
    relative_to_name: str | None = None
    home: str | None = None                          # "me" (or relative-to), when they're in the export
    stops: set = field(default_factory=set)
    leave_out: set = field(default_factory=set)

    def stats(self) -> dict:
        real = [p for p in self.people.values() if not p["placeholder"]]
        photos = [m for m in self.media.values() if m["kind"] == "photo"]
        docs = [m for m in self.media.values() if m["kind"] == "document"]
        return {
            "people": len(real),
            "living": sum(1 for p in real if p["living"]),
            "limited": sum(1 for p in real if p["limited"]),
            "placeholders": len(self.people) - len(real),
            "families": len(self.families),
            "photos": len(photos), "documents": len(docs),
            "bytesEstimate": sum(m["size"] for m in self.media.values()) + 6000 * len(self.people) + 60000,
        }


# ---------- scope ----------
def _neighbours(g, pid):
    return ([p for p, _f, _r in g.parents(pid)] + [c for c, _f, _r in g.children(pid)]
            + [x for x, _f in g.partners(pid) if x])


def _up(g, start, gens):
    out, frontier = {start}, [start]
    for _ in range(gens):
        nxt = [p for cur in frontier for p, _f, _r in g.parents(cur) if p not in out]
        out.update(nxt)
        frontier = nxt
    return out


def _down(g, start, gens):
    out, frontier = {start}, [start]
    for _ in range(gens):
        nxt = [c for cur in frontier for c, _f, _r in g.children(cur) if c not in out]
        out.update(nxt)
        frontier = nxt
    return out


def branch_walk(g, start, walk, partner_rule, stops, leave, keep) -> set:
    """Breadth-first walk from the start people. A stop is included but not
    expanded; a leave-out is never entered unless it keeps the chain (then it's
    walked through and shown as "Private").

    Directions: "a" goes up and down, "u" only up, "d" only down. Children are
    always walked "d" and parents keep the walk's upward mode, so "connected"
    gives the start people's blood relatives (ancestors, their descendants —
    uncles, cousins — and the start people's own descendants) and never climbs
    into the other parent's side from a child. Partners are married-in: with
    the "stop" rule they're included but not expanded; with "follow" their
    families are walked too."""
    inc, expanded = set(), set()
    q = deque()
    first = {"descendants": "d", "ancestors": "u", "both": "ud", "connected": "a"}[walk]

    def add(pid, dirs, blood=True):
        if pid not in g.people:
            return
        if pid in leave and pid not in keep:
            return
        inc.add(pid)
        if pid in stops and pid not in start:
            return
        if not blood and partner_rule == "stop":
            return
        if (pid, dirs) in expanded:
            return
        expanded.add((pid, dirs))
        q.append((pid, dirs))

    for s in start:
        add(s, first)
    while q:
        pid, dirs = q.popleft()
        if "u" in dirs or "a" in dirs:
            for par, _f, _r in g.parents(pid):
                add(par, "a" if "a" in dirs else "u")
        if "d" in dirs or "a" in dirs:
            for c, _f, _r in g.children(pid):
                add(c, "d")
        if partner_rule != "exclude":
            for x, _f in g.partners(pid):
                if x:
                    add(x, "a" if first == "a" else dirs, blood=partner_rule == "follow")
    return inc


def _scope(g, sc: ScopeIn, warnings) -> tuple[set, set, set, set]:
    """→ (included, keep-chain placeholders, stop ids, leave-out ids)"""
    known = lambda pid, what: pid in g.people or warnings.append(f"{what} isn't in the tree any more and was skipped.")
    if sc.type == "whole":
        return set(g.people), set(), set(), set()
    if sc.type == "selected":
        ids = [i for i in sc.ids if known(i, "A chosen person")]
        return set(ids), set(), set(), set()
    if sc.type in ("ancestors", "descendants", "both"):
        if not sc.personId or not known(sc.personId, "The starting person"):
            warnings.append("Choose who the export starts from.")
            return set(), set(), set(), set()
        inc = set()
        if sc.type in ("ancestors", "both"):
            inc |= _up(g, sc.personId, sc.generations)
        if sc.type in ("descendants", "both"):
            inc |= _down(g, sc.personId, sc.generations)
        if sc.partners:
            inc |= {x for pid in list(inc) for x, _f in g.partners(pid) if x}
        return inc, set(), set(), set()
    # branch
    start = [s for s in sc.start if known(s, "A starting person")]
    if not start:
        warnings.append("Choose at least one person to start the branch from.")
        return set(), set(), set(), set()
    stops = {s for s in sc.stops if known(s, "A stop point")}
    leave = {l.id for l in sc.leaveOut if known(l.id, "A left-out person")}
    keep = {l.id for l in sc.leaveOut if l.keepChain and l.id in leave}
    inc = branch_walk(g, set(start), sc.walk, sc.partnerRule, stops, leave, keep)
    return inc, keep & inc, stops, leave


def _placeholders_needed(g, kept: set, removed: set) -> set:
    """Removed people who are the only link between people who stay."""
    comp, n = {}, 0
    for pid in kept:
        if pid in comp:
            continue
        stack = [pid]
        comp[pid] = n
        while stack:
            cur = stack.pop()
            for nb in _neighbours(g, cur):
                if nb in kept and nb not in comp:
                    comp[nb] = n
                    stack.append(nb)
        n += 1
    out = set()
    for r in removed:
        comps = {comp[nb] for nb in _neighbours(g, r) if nb in comp}
        if len(comps) >= 2:
            out.add(r)
    return out


# ---------- details ----------
def _date(ev, mode):
    if not ev or not ev.get("date_text") or mode == "none":
        return None
    if mode == "year":
        return str(ev["date_y"]) if ev.get("date_y") else None
    return dates.display(ev)


def _date_time(ev, mode):
    """The date, with the time of a birth or death when full dates are exported."""
    d = _date(ev, mode)
    return f"{d}, {ev['time']}" if d and mode == "full" and ev.get("time") else d


def build(conn, options: ExportOptions, me_id: str | None) -> View:
    from . import features
    on = features.states()
    # switched-off modules (Features) are hidden in the app, so they stay out of every export too
    options = options.model_copy(deep=True)
    if not on["contacts"]:
        options.contacts = False
    if not on["sources"]:
        options.sources = False
    v = View()
    g = graph_mod.get(conn)
    inc, keep_chain, v.stops, v.leave_out = _scope(g, options.scope, v.warnings)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM people WHERE deleted_at IS NULL")}
    # keep-out flag
    flagged = {pid for pid in inc if rows[pid]["never_export"]}
    alive = {pid: graph_mod.living(g.people[pid]) for pid in inc}
    excluded_living = {pid for pid in inc if alive[pid]} if options.living == "exclude" else set()
    removed = flagged | excluded_living
    kept = inc - removed - keep_chain
    placeholders = keep_chain | _placeholders_needed(g, kept, removed)
    if flagged:
        v.warnings.append(f"{len(flagged)} {'person is' if len(flagged) == 1 else 'people are'} marked "
                          "“keep out of exports” and left out.")
    limited = {pid for pid in kept if alive[pid]} if options.living == "limited" else set()

    rel_to = options.relativeTo or me_id
    v.home = rel_to if rel_to in kept else None
    if options.relationships and len(kept) > MAX_RELATIONSHIP_LABELS:
        v.warnings.append("Too many people for relationship labels; they're left out.")
    elif options.relationships and rel_to in kept:
        v.relative_to, v.relative_to_name = rel_to, g.people[rel_to].name
    elif options.relationships and rel_to:
        # their name would appear on every page ("…'s grandfather"), so no labels at all
        v.warnings.append("Relationship labels are left out: the person they're relative to isn't in this export.")

    events_by_person, events_by_family = {}, {}
    for e in conn.execute("SELECT * FROM events"):
        if e["person_id"]:
            events_by_person.setdefault(e["person_id"], []).append(dict(e))
        else:
            events_by_family.setdefault(e["family_id"], []).append(dict(e))
    allowed_types = {t for grp, on in options.events.items() if on and grp in EVENT_GROUPS for t in EVENT_GROUPS[grp]}
    if not options.deaths:
        allowed_types -= {"death", "burial"}
    if not options.familyDetails:
        allowed_types -= {"divorce"}
    if not on["ceremonies"]:
        allowed_types -= set(features.CEREMONY_TYPES)
    label_graph = _label_graph(g, options) if v.relative_to else g

    def event_items(evs, hide):
        out = []
        if hide:
            return out
        for e in sorted(evs, key=lambda e: (e["sort_key"] is None, e["sort_key"] or "")):
            if e["type"] not in allowed_types:
                continue
            out.append({"type": e["type"], "label": EVENT_LABELS.get(e["type"], "Event"), "title": e["title"],
                        "date": _date_time(e, options.dates), "sortKey": e["sort_key"],
                        "place": e["place"] if options.places else None,
                        "description": e["description"] if options.notes else None})
        return out

    stories = {}
    if options.stories:
        for s in conn.execute("SELECT person_id, title, body FROM stories ORDER BY created_at, rowid"):
            stories.setdefault(s["person_id"], []).append({"title": s["title"], "body": s["body"]})

    cfields = [dict(r) for r in conn.execute("SELECT * FROM custom_fields WHERE archived = 0 AND applies_to = 'person' "
                                              "ORDER BY position, label COLLATE NOCASE")]
    cfields = [f for f in cfields if options.customFields.get(f["id"], bool(f["export_default"]))] if on["custom_fields"] else []
    cvalues = {}
    if cfields:
        ids = {f["id"] for f in cfields}
        for r in conn.execute("SELECT person_id, field_id, value FROM custom_values WHERE person_id IS NOT NULL"):
            if r["field_id"] in ids:
                cvalues.setdefault(r["person_id"], {})[r["field_id"]] = r["value"]

    for pid in kept | placeholders:
        p = g.people[pid]
        if pid in placeholders:
            v.people[pid] = {"id": pid, "placeholder": True, "limited": True, "living": False, "died": False, "name": "Private",
                             "given": "Private", "surname": None, "gender": "unknown", "years": "", "events": [],
                             "birth": None, "death": None, "photo": None, "relationship": None}
            continue
        r = rows[pid]
        lim = pid in limited
        bd_on = "birth" in allowed_types
        died = not alive[pid] and options.deaths
        item = {
            "id": pid, "placeholder": False, "limited": lim, "living": alive[pid],
            "name": p.name, "given": p.given, "surname": p.surname,
            "gender": p.gender if options.gender else "unknown", "died": died,
            "birthSurname": r["birth_surname"] if options.maidenNames and not lim else None,
            "nickname": r["nickname"] if options.otherNames and not lim else None,
            "otherNames": ([n["name"] for n in (json.loads(r["other_names"]) or [])]
                           if options.otherNames and r["other_names"] and not lim else []),
            "birth": None, "death": None, "events": [], "biography": None, "stories": [], "photo": None,
            "deceased": died, "relationship": None,
        }
        if not lim and bd_on:
            for kind in ("birth", "death") if options.deaths else ("birth",):
                ev = getattr(p, kind)
                if ev:
                    item[kind] = {"date": _date(ev, options.dates), "year": ev.get("date_y") if options.dates != "none" else None,
                                  "time": ev.get("time") if options.dates == "full" else None,
                                  "place": ev.get("place") if options.places else None}
        by = item["birth"]["year"] if item["birth"] else None
        dy = item["death"]["year"] if item["death"] else None
        item["years"] = "" if lim else (f"{by or ''}–{dy or ''}" if (by or dy) and died
                                        else (f"b. {by}" if by else ""))
        item["events"] = event_items(events_by_person.get(pid, []), lim)
        if not lim and options.stories:
            item["biography"] = r["biography"]
            item["stories"] = stories.get(pid, [])
        item["custom"] = [] if lim else [
            {"label": f["label"], "value": dates.field_display(f["kind"], cvalues[pid][f["id"]])}
            for f in cfields if cvalues.get(pid, {}).get(f["id"])
            and not (f["kind"] == "place" and not options.places) and not (f["kind"] == "date" and options.dates == "none")]
        item["contacts"] = []
        if options.contacts and alive[pid] and not lim:
            item["contacts"] = [{"kind": c["kind"], "label": c["label"], "value": c["value"]} for c in conn.execute(
                "SELECT kind, label, value FROM contacts WHERE person_id = ? ORDER BY position, rowid", (pid,))]
        item["sources"] = []
        if options.sources and not lim:
            item["sources"] = _sources_for(conn, pid, allowed_types)
        if v.relative_to:
            lab = relations.relationship(label_graph, v.relative_to, pid)
            item["relationship"] = (lab["label"] if lab["kind"] not in ("none", "self")
                                    and lab["label"] not in ("you", "not related") else None)
        v.people[pid] = item

    shown = set(v.people)
    private = {pid for pid, p in v.people.items() if p["limited"] or p["placeholder"]}
    for f in g.families.values():
        partners = [x for x in (f.p1, f.p2) if x in shown]
        kids = [{"id": c, "relation": rel if options.familyDetails else "birth"} for c, rel, _pos in f.children if c in shown]
        if not kids and len(partners) < 2:
            continue                                     # nothing left to connect
        # a partner who's private, or not in the export at all, hides the couple's events
        hide = any(x and (x not in shown or x in private) for x in (f.p1, f.p2))
        fam = {"id": f.id, "p1": f.p1 if f.p1 in shown else None, "p2": f.p2 if f.p2 in shown else None,
               "kind": f.kind if options.familyDetails else "unknown",
               "ended": f.ended if options.familyDetails else None, "children": kids,
               "events": event_items(events_by_family.get(f.id, []), hide)}
        v.families.append(fam)

    _pick_media(conn, options, v, private, g, allowed_types)
    if (options.photos != "none" or options.documents) and not media.is_online():
        v.warnings.append("Photo storage isn't reachable right now, so photos and documents can't be included.")
    return v




def _sources_for(conn, pid: str, allowed_types: set) -> list:
    """"Birth certificate, p. 4 — born" lines for a person's page (own facts and events only)."""
    out, seen = [], set()
    for c in conn.execute(
            "SELECT c.page, c.fact, s.title, e.type AS etype FROM citations c JOIN sources s ON s.id = c.source_id "
            "AND s.deleted_at IS NULL LEFT JOIN events e ON e.id = c.event_id WHERE c.person_id = ? OR "
            "c.event_id IN (SELECT id FROM events WHERE person_id = ?) ORDER BY c.created_at", (pid, pid)):
        if c["etype"] and c["etype"] not in allowed_types:
            continue
        what = c["fact"] or (EVENT_LABELS.get(c["etype"], "").lower() if c["etype"] else None)
        key = (c["title"], c["page"], what)
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": c["title"], "page": c["page"], "fact": what})
    return out


def _label_graph(g, options):
    """The graph relationship labels are worked out on: with gender or family
    details switched off, a copy without them, so "mother", "ex-wife" or
    "stepson" can't give away what was left out ("parent", "spouse", "son")."""
    if options.gender and options.familyDetails:
        return g
    people = {pid: replace(p, gender=p.gender if options.gender else "unknown",
                           parent_fams=p.parent_fams if options.familyDetails else [(f, "birth") for f, _r in p.parent_fams])
              for pid, p in g.people.items()}
    fams = g.families if options.familyDetails else {
        fid: replace(f, kind="married", ended=None, children=[(c, "birth", pos) for c, _r, pos in f.children])
        for fid, f in g.families.items()}
    return graph_mod.Graph(people, fams)


def _pick_media(conn, options, v: View, private: set, g, allowed_types: set):
    """Photos and documents that can be shown, and the pages they appear on.

    A file is left out when anyone linked to it isn't shown in full (limited,
    "Private", keep-out, or simply not in this export), when it's linked to a
    family whose partners aren't all shown in full, or when it would appear on
    no page at all (for example it's only linked to an event type that's off)."""
    if options.photos == "none" and not options.documents:
        return
    shown_real = {pid for pid, p in v.people.items() if not p["placeholder"] and not p["limited"]}
    fam_pages = {}                                      # family id → partner pages, if every partner is shown
    for f in g.families.values():
        ps = [x for x in (f.p1, f.p2) if x]
        if ps and all(x in shown_real for x in ps):
            fam_pages[f.id] = ps
    ev_pages = {}                                       # event id → pages, for events that are exported
    for e in conn.execute("SELECT id, type, person_id, family_id FROM events"):
        if e["type"] not in allowed_types:
            continue
        if e["person_id"] in shown_real:
            ev_pages[e["id"]] = [e["person_id"]]
        elif e["family_id"] in fam_pages:
            ev_pages[e["id"]] = fam_pages[e["family_id"]]
    links = {}
    for l in conn.execute("SELECT ml.* FROM media_links ml JOIN media m ON m.id = ml.media_id WHERE m.deleted_at IS NULL"):
        links.setdefault(l["media_id"], []).append(l)
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM media WHERE deleted_at IS NULL")}
    profiles = {}
    for pid in shown_real:
        prof = conn.execute("SELECT photo_media_id FROM people WHERE id = ?", (pid,)).fetchone()[0]
        if prof:
            profiles.setdefault(prof, []).append(pid)
    for mid, ls in links.items():
        m = rows.get(mid)
        if not m:
            continue
        # anyone in it who isn't shown in full keeps the whole file out
        if any(l["person_id"] not in shown_real for l in ls if l["person_id"]):
            continue
        if any(l["family_id"] not in fam_pages for l in ls if l["family_id"]):
            continue
        pages = set()
        for l in ls:
            if l["person_id"]:
                pages.add(l["person_id"])
            elif l["family_id"]:
                pages.update(fam_pages[l["family_id"]])
            elif l["event_id"] in ev_pages:
                pages.update(ev_pages[l["event_id"]])
        if not pages:
            continue
        if m["kind"] == "photo":
            if options.photos == "none":
                continue
            if options.photos == "profile" and mid not in profiles:
                continue
            size_key = "1024" if options.photoSize == "web" else "original"
        else:
            if not options.documents:
                continue
            size_key = "original"
        edit = json.loads(m["edit"]) if m["kind"] == "photo" and m["edit"] else None     # photo fixes (§13.19)
        try:
            path = media.served_path(mid, "display" if edit and size_key == "original" else size_key, edit)
        except Exception:
            path = media.file_path(mid, size_key)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = m["size"]
        tags = sorted({l["person_id"] for l in ls if l["person_id"]})
        ctype = m["content_type"] if size_key == "original" and not edit else "image/jpeg"
        v.media[mid] = {"id": mid, "kind": m["kind"], "title": m["title"],
                        "date": _date(dates.parse_text(m["date_text"]) if m["date_text"] and _parses(m["date_text"]) else None, options.dates),
                        "description": m["description"] if options.notes else None,
                        "contentType": ctype, "size": size, "sizeKey": size_key, "path": path, "tags": tags,
                        "pages": sorted(pages)}
    for mid, pids in profiles.items():
        if mid in v.media and v.media[mid]["kind"] == "photo":
            for pid in pids:
                v.people[pid]["photo"] = mid
                reg = conn.execute("SELECT photo_region_id FROM people WHERE id = ?", (pid,)).fetchone()[0]
                v.people[pid]["photoRegion"] = reg
    for pid in shown_real:
        v.people[pid]["media"] = [mid for mid, m in v.media.items() if pid in m["pages"]]


def _parses(text) -> bool:
    try:
        dates.parse_text(text)
        return True
    except dates.DateError:
        return False


def included_ids(v: View) -> dict:
    return {"included": sorted(p for p, x in v.people.items() if not x["placeholder"]),
            "placeholderIds": sorted(p for p, x in v.people.items() if x["placeholder"]),
            "stops": sorted(v.stops), "leaveOut": sorted(v.leave_out)}


def summary_label(options: ExportOptions, stats: dict, fmt: str) -> str:
    what = {"site": "the website"}.get(fmt, fmt)
    return (f"Exported {what}: {stats['people']} people (living: {options.living}, "
            f"photos: {options.photos})")
