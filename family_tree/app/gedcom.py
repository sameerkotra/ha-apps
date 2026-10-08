"""GEDCOM (§14): the family as a `.ged` file other genealogy programs read, and a `.ged` file read back in.

Both directions go through the tree data of the website export (`tree_data`):

- **Export** writes GEDCOM 5.5.1 (UTF-8) from `tree_data.export_data` — the same filtered projection as the website,
  so whatever the Export choices leave out (living people's details, places, notes, stories …) stays out. Photos
  aren't in the file. Things GEDCOM has no tag for are kept in `_FT_…` tags that other programs skip and this app
  reads back: the person's and family's own ids (`_FT_ID`), event types (`_FT_TYPE`, e.g. the Indian ceremonies,
  written as `EVEN` with a `TYPE`), a step child (`_PEDI step`), partners who aren't married (`_FT_KIND`), stories
  (`_STORY`) and which install the file came from (`HEAD._FT_SOURCE`).
- **Import** turns a `.ged` (5.5.1 or 7.0; UTF-8, UTF-16 or an 8-bit character set) into that same tree data, so
  Admin → Import shows its usual preview, matches people already here and adds everything as one undoable batch.
  Importing a newer file from the same place again only adds what's new. Read: names (given, surname, nickname,
  birth/maiden and other names), sex, the usual life and family events with dates, places and notes, "died",
  notes (as the biography), parents and children (birth, adopted, step, foster), marriages and divorces.
  Not read: sources, media files, addresses and LDS ordinances.
"""
import hashlib
import re

from . import config, dates, tree_data
from .ceremonies import ALL as CEREMONIES
from .models import FAMILY_EVENT_TYPES, PERSON_EVENT_TYPES

MAX_LINE = 200                 # characters of a value per line (5.5.1 allows 255 for the whole line)
MAX_BYTES = 100 * 1024 * 1024

# our event type → (GEDCOM tag, TYPE for EVEN)
PERSON_TAGS = {"birth": "BIRT", "death": "DEAT", "burial": "BURI", "baptism": "BAPM", "education": "EDUC",
               "occupation": "OCCU", "residence": "RESI", "immigration": "IMMI", "emigration": "EMIG",
               "religion": "RELI", "retirement": "RETI"}
FAMILY_TAGS = {"marriage": "MARR", "engagement": "ENGA", "divorce": "DIV", "residence": "RESI"}
# GEDCOM tag → our type, when reading (more tags than we write)
READ_PERSON = {v: k for k, v in PERSON_TAGS.items()}
READ_PERSON.update({"CHR": "baptism", "CREM": "burial", "GRAD": "education", "NATU": "immigration", "CENS": "residence",
                    "PROP": "custom", "ORDN": "religion", "CONF": "religion", "FCOM": "religion", "BARM": "religion",
                    "BASM": "religion", "BLES": "religion", "ADOP": "custom", "WILL": "custom", "PROB": "custom",
                    "TITL": "custom", "DSCR": "custom", "NATI": "custom", "CAST": "custom", "SSN": None, "IDNO": None,
                    "EVEN": "custom", "FACT": "custom", "_MILT": "military", "_MILI": "military"})
READ_FAMILY = {v: k for k, v in FAMILY_TAGS.items()}
READ_FAMILY.update({"MARB": "engagement", "MARC": "custom", "MARL": "custom", "MARS": "custom", "ANUL": "divorce",
                    "DIVF": "divorce", "EVEN": "custom", "FACT": "custom", "CENS": "residence"})
EVENT_TITLES = {"military": "Military service", "CREM": "Cremation", "ADOP": "Adoption", "WILL": "Will",
                "PROB": "Probate", "TITL": "Title", "DSCR": "Description", "NATI": "Nationality", "CAST": "Caste",
                "PROP": "Property", "MARC": "Marriage contract", "MARL": "Marriage licence", "MARS": "Marriage settlement",
                "ORDN": "Ordination", "CONF": "Confirmation", "FCOM": "First communion", "BARM": "Bar mitzvah",
                "BASM": "Bat mitzvah", "BLES": "Blessing", "ANUL": "Annulment"}
PEDI_OUT = {"adopted": "adopted", "foster": "foster", "birth": "birth"}
PEDI_IN = {"adopted": "adopted", "foster": "foster", "birth": "birth", "natural": "birth", "step": "step",
           "stepchild": "step", "_step": "step"}
SEX_OUT = {"male": "M", "female": "F", "other": "X", "unknown": "U"}
SEX_IN = {"M": "male", "F": "female", "X": "other", "U": "unknown"}


class GedcomError(tree_data.ImportError_):
    """A readable 422."""


# =====================================================================================================
# writing
# =====================================================================================================
def _lines(level: int, tag: str, value: str | None = None, xref: str | None = None) -> list:
    """One GEDCOM line, with CONT for line breaks and CONC for long lines."""
    head = f"{level} " + (f"{xref} " if xref else "") + tag
    if value is None or value == "":
        return [head]
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for i, part in enumerate(text.split("\n")):
        chunks = [part[j:j + MAX_LINE] for j in range(0, len(part), MAX_LINE)] or [""]
        for k, chunk in enumerate(chunks):
            if i == 0 and k == 0:
                out.append(f"{head} {chunk}".rstrip() if chunk else head)
            else:
                t = "CONT" if k == 0 else "CONC"
                out.append(f"{level + 1} {t} {chunk}".rstrip() if chunk else f"{level + 1} {t}")
    return out


def _date_out(d: dict | None) -> str | None:
    if not d or not d.get("date_text"):
        return None
    text = d["date_text"]
    if d.get("date_y") is None and d.get("date_m") and d.get("date_d"):
        return f"({text})"                  # a day and month without a year: only a date phrase in GEDCOM
    return text


def _event_out(e: dict, level: int, tags: dict) -> list:
    t = e["type"]
    if t == "occupation":
        out = _lines(level, "OCCU", e.get("title"))
    elif t in tags:
        out = _lines(level, tags[t])
        if e.get("title"):
            out += _lines(level + 1, "TYPE", e["title"])
    else:                                       # custom, military, ceremonies: EVEN with a TYPE
        label = e.get("title") or (CEREMONIES[t]["en"] if t in CEREMONIES else EVENT_TITLES.get(t)) or "Event"
        out = _lines(level, "EVEN") + _lines(level + 1, "TYPE", label)
        if t != "custom":
            out += _lines(level + 1, "_FT_TYPE", t)
    date = _date_out(e.get("date"))
    if date:
        out += _lines(level + 1, "DATE", date)
        if e.get("time"):
            out += _lines(level + 2, "TIME", e["time"])
    if e.get("place"):
        out += _lines(level + 1, "PLAC", e["place"])
    if e.get("description"):
        out += _lines(level + 1, "NOTE", e["description"])
    return out


def write(data: dict, app_version: str | None = None) -> str:
    """Tree data (`tree_data.export_data`) as a GEDCOM 5.5.1 file."""
    src = data.get("source") or {}
    people = data.get("people") or []
    families = data.get("families") or []
    pref = {p["id"]: f"@I{i + 1}@" for i, p in enumerate(people)}
    fref = {f["id"]: f"@F{i + 1}@" for i, f in enumerate(families)}
    famc, fams = {}, {}
    for f in families:
        for k in ("p1", "p2"):
            if f.get(k) in pref:
                fams.setdefault(f[k], []).append(f["id"])
        for c in f.get("children") or []:
            if c["id"] in pref:
                famc.setdefault(c["id"], []).append((f["id"], c.get("relation") or "birth"))
    now = config.now()
    out = ["0 HEAD", "1 SOUR FAMILY_TREE", "2 NAME Family Tree for Home Assistant"]
    if app_version:
        out.append(f"2 VERS {app_version}")
    out += [f"1 DATE {now.day} {dates.MONTHS[now.month - 1]} {now.year}", "1 SUBM @U1@", "1 GEDC", "2 VERS 5.5.1",
            "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8", "1 FILE family-tree.ged"]
    if src.get("id"):
        out.append(f"1 _FT_SOURCE {src['id']}")
    out += _lines(0, "SUBM", None, "@U1@") + _lines(1, "NAME", src.get("title") or "Family Tree")
    for p in people:
        out += _lines(0, "INDI", None, pref[p["id"]])
        given, surname = p.get("given_names") or "", p.get("surname") or ""
        out += _lines(1, "NAME", f"{given} /{surname}/".strip())
        if given:
            out += _lines(2, "GIVN", given)
        if surname:
            out += _lines(2, "SURN", surname)
        if p.get("nickname"):
            out += _lines(2, "NICK", p["nickname"])
        if p.get("birth_surname") and p["birth_surname"] != surname:
            out += _lines(1, "NAME", f"{given} /{p['birth_surname']}/".strip()) + _lines(2, "TYPE", "birth")
        for n in p.get("other_names") or []:
            kind = n.get("type") if n.get("type") in ("aka", "birth", "married", "religious") else "aka"
            out += _lines(1, "NAME", n["name"]) + _lines(2, "TYPE", kind)
        out += _lines(1, "SEX", SEX_OUT.get(p.get("gender"), "U"))
        evs = p.get("events") or []
        for e in evs:
            out += _event_out(e, 1, PERSON_TAGS)
        if p.get("deceased") and not any(e["type"] == "death" for e in evs):
            out += _lines(1, "DEAT", "Y")
        if p.get("biography"):
            out += _lines(1, "NOTE", p["biography"])
        for s in p.get("stories") or []:
            out += _lines(1, "_STORY", s["body"]) + _lines(2, "TITL", s.get("title") or "Story")
        for fid, rel in famc.get(p["id"], []):
            out += _lines(1, "FAMC", fref[fid])
            if rel == "step":
                out += _lines(2, "_PEDI", "step")
            elif rel in PEDI_OUT:
                out += _lines(2, "PEDI", PEDI_OUT[rel])
        for fid in fams.get(p["id"], []):
            out += _lines(1, "FAMS", fref[fid])
        out += _lines(1, "_FT_ID", p["id"])
    genders = {p["id"]: p.get("gender") for p in people}
    for f in families:
        out += _lines(0, "FAM", None, fref[f["id"]])
        a, b = (x if x in pref else None for x in (f.get("p1"), f.get("p2")))
        if a and b:
            if genders.get(a) == "female" and genders.get(b) != "female":
                a, b = b, a                     # 5.5.1 has HUSB and WIFE: a woman goes in WIFE where it can
            out += _lines(1, "HUSB", pref[a]) + _lines(1, "WIFE", pref[b])
        elif a or b:
            x = a or b
            out += _lines(1, "WIFE" if genders.get(x) == "female" else "HUSB", pref[x])
        for c in f.get("children") or []:
            if c["id"] in pref:
                out += _lines(1, "CHIL", pref[c["id"]])
        evs = f.get("events") or []
        for e in evs:
            out += _event_out(e, 1, FAMILY_TAGS)
        if f.get("kind") == "married" and not any(e["type"] == "marriage" for e in evs):
            out += _lines(1, "MARR", "Y")
        elif f.get("kind") == "partners":
            out += _lines(1, "_FT_KIND", "partners")
        if f.get("ended") == "divorced" and not any(e["type"] == "divorce" for e in evs):
            out += _lines(1, "DIV", "Y")
        elif f.get("ended") in ("separated", "widowed"):
            out += _lines(1, "_FT_ENDED", f["ended"])
        out += _lines(1, "_FT_ID", f["id"])
    out.append("0 TRLR")
    return "\n".join(out) + "\n"


# =====================================================================================================
# reading
# =====================================================================================================
class Node:
    __slots__ = ("level", "xref", "tag", "value", "children")

    def __init__(self, level, xref, tag, value):
        self.level, self.xref, self.tag, self.value, self.children = level, xref, tag, value, []

    def first(self, tag):
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag):
        return [c for c in self.children if c.tag == tag]

    def text(self, tag):
        c = self.first(tag)
        return c.value.strip() if c is not None and c.value else None


_LINE = re.compile(r"^\s*(\d{1,2})\s+(?:(@[^@\s]{1,60}@)\s+)?([A-Za-z0-9_]{1,31})(?:\s(.*))?$")


def looks_like(head: bytes) -> bool:
    """Is this the start of a GEDCOM file (any of its encodings)?"""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if head.startswith(bom):
            head = head[len(bom):].decode(enc, "ignore").encode()
            break
    else:
        if len(head) > 1 and head[1:2] == b"\0":
            head = head.decode("utf-16-le", "ignore").encode()
        elif head[:1] == b"\0":
            head = head.decode("utf-16-be", "ignore").encode()
    return re.match(rb"^\s*0\s+HEAD\b", head) is not None


def decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")) or raw[1:2] == b"\0" or raw[:1] == b"\0":
        enc = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-16-le" if raw[1:2] == b"\0" else "utf-16-be")
        return raw.decode(enc, "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")      # ANSI / ANSEL: plain letters right, some accents may not be


def parse(text: str) -> list:
    """The records (level-0 nodes) of a GEDCOM file, CONT/CONC joined into the values."""
    roots, stack = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        m = _LINE.match(raw)
        if not m:
            raise GedcomError(f"Line {n} of that GEDCOM file couldn't be read.")
        level, xref, tag, value = int(m.group(1)), m.group(2), m.group(3).upper(), m.group(4) or ""
        if tag in ("CONT", "CONC") and stack:
            parent = stack[-1] if stack[-1].level == level - 1 else next((s for s in reversed(stack) if s.level == level - 1), None)
            if parent is not None:
                parent.value = (parent.value or "") + ("\n" if tag == "CONT" else "") + value
                continue
        node = Node(level, xref, tag, value)
        while stack and stack[-1].level >= level:
            stack.pop()
        if level == 0:
            roots.append(node)
        elif stack and stack[-1].level == level - 1:
            stack[-1].children.append(node)
        else:
            raise GedcomError(f"Line {n} of that GEDCOM file is at the wrong level.")
        stack.append(node)
    if not roots or roots[0].tag != "HEAD":
        raise GedcomError("That isn't a GEDCOM file (it doesn't start with a HEAD record).")
    return roots


_ID_BAD = re.compile(r"[^A-Za-z0-9_-]")


def _rid(prefix: str, xref: str | None, ft_id: str | None, taken: set) -> str:
    if ft_id and tree_data._ID_RE.match(ft_id) and ft_id not in taken:
        rid = ft_id
    else:
        base = prefix + _ID_BAD.sub("_", (xref or "").strip("@"))[:58]
        rid, n = base, 1
        while rid in taken or not tree_data._ID_RE.match(rid):
            n += 1
            rid = f"{base[:56]}_{n}"
    taken.add(rid)
    return rid


def _note_text(node: Node, notes: dict) -> str | None:
    v = (node.value or "").strip()
    if re.match(r"^@[^@]+@$", v):
        return notes.get(v)
    return v or None


def _date_in(raw: str | None) -> tuple:
    """(date_text for the app, or None; the raw text when it couldn't be read)."""
    if not raw:
        return None, None
    t = raw.strip()
    t = re.sub(r"^@#D[A-Z ]+@\s*", "", t)                     # a calendar escape (@#DGREGORIAN@)
    m = re.match(r"^INT\s+(.*?)\s*\(.*\)$", t, re.I)
    if m:
        t = m.group(1)
    if t.startswith("(") and t.endswith(")"):
        t = t[1:-1]
    t = re.sub(r"^(FROM|TO)\s+", "", t, flags=re.I).split(" TO ")[0]
    try:
        cols = dates.parse_text(t)
    except dates.DateError:
        cols = None
    return (cols["date_text"] if cols else None), (None if cols else raw.strip())


def _event_in(node: Node, kind: str, eid: str, notes: dict) -> dict | None:
    table = READ_PERSON if kind == "person" else READ_FAMILY
    allowed = PERSON_EVENT_TYPES if kind == "person" else FAMILY_EVENT_TYPES
    etype = table.get(node.tag)
    if etype is None:
        return None
    ft = node.text("_FT_TYPE")
    if ft in allowed:
        etype = ft
    if etype not in allowed:
        etype = "custom"
    title = node.text("TYPE")
    if node.tag == "OCCU":
        title = (node.value or "").strip() or title
    elif node.tag in ("TITL", "DSCR", "NATI", "CAST", "PROP", "FACT", "EVEN") and (node.value or "").strip() not in ("", "Y"):
        title = f"{title}: {node.value.strip()}" if title else node.value.strip()
    if etype == "custom" and not title:
        title = EVENT_TITLES.get(node.tag) or node.tag.title()
    if etype in ("military",) and not title:
        title = EVENT_TITLES["military"]
    if etype in CEREMONIES and title == CEREMONIES[etype]["en"]:
        title = None
    date_text, unread = _date_in(node.text("DATE"))
    desc = []
    for c in node.all("NOTE"):
        t = _note_text(c, notes)
        if t:
            desc.append(t)
    if unread:
        desc.append(f"Date: {unread}")
    plac = node.text("PLAC")
    time = None
    d = node.first("DATE")
    if d is not None:
        time = d.text("TIME")
    out = {"id": eid, "type": etype, "title": title, "place": plac, "description": "\n\n".join(desc) or None,
           "date": {"date_text": date_text} if date_text else None,
           "time": time[:5] if time and re.match(r"^\d{2}:\d{2}", time) else None}
    if not (out["date"] or out["place"] or out["description"] or out["title"] or (node.value or "").strip() == "Y"
            or etype in ("birth", "death", "burial", "marriage", "divorce")):
        return None
    return out


def _name_parts(node: Node) -> tuple:
    """(given, surname) of a NAME: its GIVN/SURN parts, else "Given /Surname/ suffix"."""
    v = (node.value or "").strip()
    m = re.match(r"^(.*?)/(.*?)/(.*)$", v)
    given = node.text("GIVN") or ((m.group(1) + " " + m.group(3)).strip() if m else v) or None
    surname = node.text("SURN") or ((m.group(2).strip() or None) if m else None)
    return (" ".join(given.split()) if given else None), (" ".join(surname.split()) if surname else None)


def to_tree_data(raw: bytes, file_name: str | None = None) -> dict:
    """A GEDCOM file as tree data for `tree_data.validate` (and so the import's preview and apply)."""
    if len(raw) > MAX_BYTES:
        raise GedcomError("That GEDCOM file is too large to import.")
    records = parse(decode(raw))
    head = records[0]
    notes = {r.xref: (r.value or "").strip() for r in records if r.tag in ("NOTE", "SNOTE") and r.xref}
    indis = [r for r in records if r.tag == "INDI" and r.xref]
    fams = [r for r in records if r.tag == "FAM" and r.xref]
    if len(indis) > tree_data.MAX_PEOPLE:
        raise GedcomError(f"An import can have at most {tree_data.MAX_PEOPLE} people.")
    if not indis:
        raise GedcomError("That GEDCOM file has nobody in it.")
    taken: set = set()
    pid = {r.xref: _rid("I", r.xref, r.text("_FT_ID"), taken) for r in indis}
    fid = {r.xref: _rid("F", r.xref, r.text("_FT_ID"), taken) for r in fams}
    pedi = {}                                     # (child xref, family xref) → relation
    people = []
    for r in indis:
        names_ = r.all("NAME")
        main = next((n for n in names_ if (n.text("TYPE") or "").lower() not in ("birth", "maiden", "aka", "married", "religious")
                     and not n.text("_LANG") and not n.text("LANG")), names_[0] if names_ else None)
        given, surname = _name_parts(main) if main is not None else (None, None)
        nickname = (main.text("NICK") if main is not None else None) or r.text("NICK")
        birth_surname, other = None, []
        for n in names_:
            if n is main:
                continue
            kind = (n.text("TYPE") or "aka").lower()
            g, s = _name_parts(n)
            if kind in ("birth", "maiden") and s and s != surname and not birth_surname:
                birth_surname = s
                continue
            full = " ".join(x for x in (g, s) if x)
            if full:
                other.append({"type": kind if kind in ("aka", "married", "birth", "religious") else "aka", "name": full[:100]})
        events, n_ev = [], 0
        deceased = False
        for c in r.children:
            if c.tag in READ_PERSON:
                n_ev += 1
                e = _event_in(c, "person", f"{pid[r.xref]}-e{n_ev}"[:64], notes)
                if e:
                    events.append(e)
                if c.tag in ("DEAT", "BURI", "CREM"):
                    deceased = True
        bio = [t for t in (_note_text(c, notes) for c in r.all("NOTE")) if t]
        stories = [{"id": f"{pid[r.xref]}-s{i + 1}"[:64], "title": c.text("TITL") or "Story", "body": c.value}
                   for i, c in enumerate(r.all("_STORY")) if (c.value or "").strip()]
        for c in r.all("FAMC"):
            rel = (c.text("PEDI") or c.text("_PEDI") or "").lower()
            if rel in PEDI_IN:
                pedi[(r.xref, (c.value or "").strip())] = PEDI_IN[rel]
        people.append({"id": pid[r.xref], "given_names": given, "surname": surname, "nickname": nickname,
                       "birth_surname": birth_surname, "other_names": other[:10],
                       "gender": SEX_IN.get((r.text("SEX") or "U")[:1].upper(), "unknown"), "deceased": deceased,
                       "biography": "\n\n".join(bio) or None, "events": events, "stories": stories, "photo": None})
    families = []
    for r in fams:
        partners = [pid.get((c.value or "").strip()) for c in r.children if c.tag in ("HUSB", "WIFE")]
        partners = [x for x in partners if x][:2]
        kids = []
        for c in r.all("CHIL"):
            x = (c.value or "").strip()
            if x not in pid:
                continue
            rel = pedi.get((x, r.xref))
            if rel is None:                        # Family Tree Maker and others: _FREL / _MREL under CHIL
                frel = (c.text("_FREL") or c.text("_MREL") or "").lower()
                rel = PEDI_IN.get(frel)
            kids.append({"id": pid[x], "relation": rel or "birth"})
        events, n_ev = [], 0
        kind, ended = "unknown", None
        for c in r.children:
            if c.tag in READ_FAMILY:
                n_ev += 1
                e = _event_in(c, "family", f"{fid[r.xref]}-e{n_ev}"[:64], notes)
                if e:
                    events.append(e)
                if c.tag in ("MARR", "MARB", "MARC", "MARL", "MARS"):
                    kind = "married"
                if c.tag in ("DIV", "DIVF", "ANUL"):
                    ended = "divorced"
        if r.text("_FT_KIND") == "partners":
            kind = "partners"
        if r.text("_FT_ENDED") in ("separated", "widowed"):
            ended = r.text("_FT_ENDED")
        if not partners and not kids:
            continue
        families.append({"id": fid[r.xref], "p1": partners[0] if partners else None,
                         "p2": partners[1] if len(partners) > 1 else None, "kind": kind, "ended": ended,
                         "children": kids, "events": events})
    sour = head.first("SOUR")
    subm_ref = head.text("SUBM")
    subm = next((r for r in records if r.tag == "SUBM" and r.xref == subm_ref), None)
    title = (subm.text("NAME") if subm is not None else None) or (sour.text("NAME") if sour is not None else None) \
        or (file_name or "").rsplit(".", 1)[0] or "GEDCOM file"
    src_id = head.text("_FT_SOURCE")
    if not (src_id and tree_data._ID_RE.match(src_id)):
        key = "|".join([(sour.value if sour is not None else "") or "", title, head.text("FILE") or file_name or ""])
        src_id = "ged-" + hashlib.sha1(key.encode()).hexdigest()[:24]
    return {"format": tree_data.FORMAT, "version": tree_data.VERSION, "app": "GEDCOM",
            "source": {"id": src_id, "title": title[:120], "exportedAt": head.text("DATE")},
            "people": people, "families": families, "media": []}
