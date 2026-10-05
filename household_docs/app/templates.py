"""Templates (SPEC §17.17): ➕ New → From a template, and ⋯ → Save as template.

- **Built in** (code, below): meeting notes, a home maintenance log (a sheet: date, what, who, cost), a monthly
  budget (a sheet with categories, totals and a "left" formula), emergency contacts, a babysitter info sheet and a
  trip plan — things Shopping and Todo don't cover (no grocery or packing lists, §1.1).
- **Yours** are files in a hidden `.templates` folder in your own folder (`people/<you>/.templates/`); **the
  household's** in the documents folder's `.templates/` (admins add and remove them). Plain files like
  everything else; hidden folders are never listed, indexed or zipped (§5.1).
- Using a template makes an ordinary document (through docops.create: the folder's rules, quota, read-only
  mode). Kids' space: no sheets (§17.20).
"""
import os

from fastapi import HTTPException

from . import docops, kids, sharing
from .formats import checklist_md, text as text_fmt
from .store import fileio, nodes, paths, roots

DIR = ".templates"
EXTS = {"txt": "note", "md": "markdown", "xlsx": "sheet", "csv": "sheet"}
MAX_TEMPLATES = 100
MAX_BYTES = 5 * 1024 * 1024


def _md(*lines) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _cell(v, **kw) -> dict:
    return {"v": v, **kw}


def _maintenance_sheet() -> dict:
    cells = {"A1": _cell("Date", b=True), "B1": _cell("What", b=True), "C1": _cell("Who", b=True),
             "D1": _cell("Cost", b=True, al="right")}
    for r in range(2, 21):
        cells[f"A{r}"] = {"f": "date"}                           # dates as yyyy-mm-dd
        cells[f"D{r}"] = {"f": "currency", "d": 2}
    return {"tabs": [{"name": "Log", "kind": "grid", "cells": cells, "cols": {"A": 110, "B": 320, "C": 140, "D": 110},
                      "freeze": {"r": 1, "c": 0}, "totals": {"D": "SUM"}, "cond": [], "charts": []}]}


BUDGET_ROWS = ("Rent or mortgage", "Electricity, water, gas", "Groceries", "Transport", "Insurance",
               "Phone and internet", "Children", "Eating out", "Savings", "Other")


def _budget_sheet() -> dict:
    cells = {"A1": _cell("Category", b=True), "B1": _cell("Planned", b=True, al="right"),
             "C1": _cell("Spent", b=True, al="right"), "D1": _cell("Left", b=True, al="right")}
    for i, name in enumerate(BUDGET_ROWS, start=2):
        cells[f"A{i}"] = _cell(name)
        cells[f"B{i}"] = _cell(0, f="currency", d=2)
        cells[f"C{i}"] = _cell(0, f="currency", d=2)
        cells[f"D{i}"] = _cell(f"=B{i}-C{i}", c=0, f="currency", d=2, red=True)
    return {"tabs": [{"name": "Budget", "kind": "grid", "cells": cells, "cols": {"A": 220, "B": 110, "C": 110, "D": 110},
                      "freeze": {"r": 1, "c": 0}, "totals": {"B": "SUM", "C": "SUM", "D": "SUM"},
                      "cond": [{"type": "lt", "range": f"D2:D{len(BUDGET_ROWS) + 1}", "a": 0, "fill": "red"}],
                      "charts": []}]}


BUILT_IN = {
    "meeting": {"name": "Meeting notes", "kind": "markdown", "about": "Who, agenda, notes, decisions and actions.",
                "content": lambda: _md("# Meeting notes", "", "**When:** ", "**Who:** ", "", "## Agenda", "- ", "",
                                       "## Notes", "", "", "## Decisions", "- ", "", "## Actions",
                                       "- [ ] Who — what — by when")},
    "maintenance": {"name": "Home maintenance log", "kind": "sheet", "about": "A sheet: date, what, who, cost.",
                    "sheet": _maintenance_sheet},
    "budget": {"name": "Monthly budget", "kind": "sheet",
               "about": "A sheet with categories, planned and spent, what's left, and totals.", "sheet": _budget_sheet},
    "contacts": {"name": "Emergency contacts", "kind": "markdown", "about": "Emergency numbers, doctors, family, "
                                                                            "neighbours and services.",
                 "content": lambda: _md("# Emergency contacts", "", "## Emergency", "- Emergency number: ",
                                        "- Poison control: ", "- Nearest hospital: ", "", "## Doctors",
                                        "- Family doctor: ", "- Dentist: ", "- Vet: ", "", "## Family and neighbours",
                                        "- ", "- ", "", "## Around the house", "- Electricity (power cut): ",
                                        "- Water: ", "- Gas: ", "- Plumber: ", "- Locksmith: ", "", "## Insurance",
                                        "- Home: ", "- Car: ")},
    "babysitter": {"name": "Babysitter info", "kind": "markdown",
                   "about": "The children, the house, the rules and who to call.",
                   "content": lambda: _md("# Babysitter info", "", "## The children", "- Name, age: ",
                                          "- Allergies and medicines: ", "- Bedtime: ", "- Food and snacks: ", "",
                                          "## The house", "- Wi-Fi: ", "- Keys and alarm: ", "- Where things are: ", "",
                                          "## Rules", "- Screens: ", "- Visitors: ", "", "## Who to call",
                                          "- Parents: ", "- Neighbour: ", "- Emergency number: ")},
    "trip": {"name": "Trip plan", "kind": "markdown", "about": "Dates, travel, where you stay, a plan per day and "
                                                                "bookings.",
             "content": lambda: _md("# Trip plan", "", "## When", "- From: ", "- To: ", "", "## Getting there",
                                    "- ", "", "## Where we stay", "- ", "", "## Day by day", "### Day 1", "- ", "",
                                    "### Day 2", "- ", "", "## Bookings and tickets", "- ", "", "## Budget", "- ", "",
                                    "## Notes", "")},
}


def built_in_bytes(key: str) -> tuple[bytes, str]:
    """(file content, extension) of a built-in template."""
    t = BUILT_IN[key]
    if t["kind"] == "sheet":
        from . import sheets
        from .formats import sheet_model as M
        return sheets.encode(M.clean_sheet(t["sheet"]()), "xlsx"), "xlsx"
    return t["content"](), "md" if t["kind"] in ("markdown", "checklist") else "txt"


# ---------------------------------------------------------------- where they are
def mine_dir(conn, user: dict) -> str:
    root = docops.my_root(conn, user)
    return os.path.join(roots.root_real(root), DIR)


def household_dir() -> str:
    return os.path.join(roots.docs_real(), DIR)


def _kind_of(path: str, ext: str) -> str:
    k = EXTS.get(ext, "file")
    if k == "markdown":
        try:
            with fileio.open_read(path) as f:
                text, _m = text_fmt.decode(f.read(MAX_BYTES))
            return checklist_md.md_kind(text, "markdown")
        except (OSError, text_fmt.NotText):
            return "markdown"
    return k


def _listing(folder: str, prefix: str) -> list[dict]:
    out = []
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.casefold())
    except OSError:
        return out
    for e in entries:
        if e.name.startswith(".") or not e.is_file(follow_symlinks=False):
            continue
        stem, ext = paths.split_ext(e.name)
        if ext.lower() not in EXTS:
            continue
        out.append({"ref": f"{prefix}:{e.name}", "name": stem, "kind": _kind_of(e.path, ext.lower()),
                    "ext": ext.lower(), "size": e.stat(follow_symlinks=False).st_size})
    return out[:MAX_TEMPLATES]


def listing(conn, user: dict) -> dict:
    child = kids.is_child(user)
    ok = lambda t: not (child and t["kind"] == "sheet")         # noqa: E731
    built = [{"ref": f"builtin:{k}", "name": t["name"], "kind": t["kind"], "about": t["about"]} for k, t in BUILT_IN.items()]
    try:
        mine = _listing(mine_dir(conn, user), "mine")
        household = _listing(household_dir(), "household")
    except roots.DocsUnavailable:
        mine, household = [], []
    return {"builtin": [t for t in built if ok(t)], "mine": [t for t in mine if ok(t)],
            "household": [t for t in household if ok(t)], "canHousehold": bool(user["is_admin"])}


def _file_of(conn, user: dict, ref: str) -> tuple[str, str]:
    """(real path, file name) of "mine:<file>" / "household:<file>", realpath-checked inside its folder."""
    kind, _, fname = (ref or "").partition(":")
    if kind not in ("mine", "household") or not fname:
        raise HTTPException(404, "That template isn't there.")
    try:
        fname = paths.check_name(fname)
    except paths.PathError:
        raise HTTPException(404, "That template isn't there.")
    folder = mine_dir(conn, user) if kind == "mine" else household_dir()
    real = paths.resolve(os.path.realpath(folder) if os.path.isdir(folder) else folder, fname)
    if not os.path.isfile(real) or os.path.islink(real):
        raise HTTPException(404, "That template isn't there.")
    return real, fname


def read(conn, user: dict, ref: str) -> tuple[bytes, str, str]:
    """(content, extension, kind) of any template."""
    if ref.startswith("builtin:"):
        key = ref[len("builtin:"):]
        if key not in BUILT_IN:
            raise HTTPException(404, "That template isn't there.")
        data, ext = built_in_bytes(key)
        return data, ext, BUILT_IN[key]["kind"]
    real, fname = _file_of(conn, user, ref)
    if os.path.getsize(real) > MAX_BYTES:
        raise HTTPException(413, "That template is too big.")
    with fileio.open_read(real) as f:
        data = f.read()
    ext = paths.split_ext(fname)[1].lower()
    return data, ext, _kind_of(real, ext)


def use(conn, user: dict, ref: str, name: str, parent_id: str | None) -> str:
    """A new document from a template, in a folder you can edit (None: My docs)."""
    data, ext, kind = read(conn, user, ref)
    kids.refuse_sheet(user, kind)
    if kind == "checklist":
        try:
            checklist_md.parse(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            kind = "markdown"
    return docops.create(conn, user, kind, name, parent_id, data, ext=ext if kind == "sheet" else
                         ("md" if kind in ("markdown", "checklist") else "txt"))


def save(conn, user: dict, node_id: str, name: str, household: bool) -> dict:
    """⋯ → Save as template: a copy of the document's file in your (or, for admins, the household's) templates."""
    node, _role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] not in ("note", "markdown", "checklist", "sheet"):
        raise HTTPException(422, "Only notes, checklists and sheets can be templates.")
    kids.refuse_sheet(user, node["kind"])
    if household and not user["is_admin"]:
        raise HTTPException(403, "Only admins can add templates for the household.")
    fileio.ensure_writable()
    root, real = nodes.real_path(conn, node)
    if (node["size"] or 0) > MAX_BYTES:
        raise HTTPException(413, "That document is too big to be a template.")
    with fileio.open_read(real) as f:
        data = f.read()
    ext = (node["ext"] or "").lower() or ("txt" if node["kind"] == "note" else "md")
    try:
        fname = paths.check_name(f"{(name or '').strip()}.{ext}")
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    folder = household_dir() if household else mine_dir(conn, user)
    os.makedirs(folder, exist_ok=True)
    folder_real = os.path.realpath(folder)
    if len([x for x in os.listdir(folder_real) if not x.startswith(".")]) >= MAX_TEMPLATES:
        raise HTTPException(409, f"There are {MAX_TEMPLATES} templates already — remove one first.")
    fname = paths.unique_name(folder_real, fname)
    my_root = docops.my_root(conn, user)
    fileio.write_atomic(my_root, paths.resolve(folder_real, fname), data)
    from . import db
    db.audit(conn, "template_saved", user["id"], node["id"], root["id"])
    return {"ref": ("household:" if household else "mine:") + fname, "name": paths.split_ext(fname)[0]}


def remove(conn, user: dict, ref: str) -> None:
    if ref.startswith("household:") and not user["is_admin"]:
        raise HTTPException(403, "Only admins can remove the household's templates.")
    if ref.startswith("builtin:"):
        raise HTTPException(422, "Built-in templates can't be removed.")
    real, _fname = _file_of(conn, user, ref)
    fileio.ensure_writable()
    os.remove(real)
    from . import db
    db.audit(conn, "template_removed", user["id"])

