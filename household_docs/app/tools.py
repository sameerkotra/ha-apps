"""What Docs answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared app/common/assist_tools.py).

- `docs.search`: the Search page's own search (search/engine.py, with its access check), first 10 results with the
  search page's snippet. A model writes "budget sheet" or "the boiler note", and the search box wants every word:
  words that only name a type ("sheet", "note", "list", "file", …) and filler words are left out; with nothing
  else left, the type's items are listed (newest first); when no item has every word, items with any of them
  are given, those with the most words first;
- `docs.read`: up to 4 KB of a note's or checklist's text, or a sheet's cells as "Tab A1: value" lines, from `offset`
  on (`more` when there is more) — read-only: opening through the assistant doesn't count as opening it here;
- `docs.checklist`: a checklist's items, ticked or open;
- `docs.note.create` (acts): a new note in My docs → Inbox, only after the person taps the proposed change.

Everything as the asking person could see it here (`sharing.require`, the 404-shaped "doesn't exist or can't be
seen"); Kids' space rules hold (no sheets for children, no making notes). Answered only while the admin's *Answer the
Household Assistant* is on and the person hasn't turned off *Let the Household Assistant answer for me* (Settings →
You). Links open the item in Docs (`/doc/<id>`, `/folder/<id>`, `/file/<id>` after the sidebar page, §6.5).
"""
import re

from fastapi import HTTPException

from . import app_messages, config, db, documents, docops, kids, links, notify, pins, settings, sharing
from .common import app_bus as bus
from .common import assist_tools
from .common.assist_tools import Arg
from .formats import checklist_md, sheet_model as M, text as text_fmt
from .search import engine
from .store import kinds, moving, nodes

READ_CHARS = 4000
SEARCH_RESULTS = 10
ID = r"[A-Za-z0-9_-]{1,64}"
KINDS = ("note", "markdown", "checklist", "sheet", "folder", "pdf", "image", "spreadsheet", "document")
# Words that only say what kind of thing is wanted, and the kinds they mean when nothing else is asked for.
TYPE_WORDS = {"sheet": ("sheet", "spreadsheet"), "spreadsheet": ("sheet", "spreadsheet"), "note": ("note", "markdown"),
              "checklist": ("checklist",), "list": ("checklist",), "folder": ("folder",), "pdf": ("pdf",),
              "doc": (), "document": (), "file": ()}
# A kind the model chose, widened to what a person means by it (a "sheet" may be an uploaded .xlsx).
KIND_MEANS = {"sheet": ("sheet", "spreadsheet"), "spreadsheet": ("sheet", "spreadsheet"), "note": ("note", "markdown")}
FILLER = {"a", "an", "the", "my", "our", "me", "about", "for", "of", "in", "on", "with", "called", "named", "titled",
          "find", "show", "open", "read", "any", "all", "some", "household", "docs"}
_WORD = re.compile(r"[^\s,;:!?\"“”()]+")


def _search_words(query: str) -> tuple[list[str], tuple | None]:
    """The words worth searching for, and the kinds the type words named (None: no type word)."""
    words, kinds_named = [], None
    for w in _WORD.findall(query):
        lw = w.lower().strip(".'’")
        base = lw[:-1] if lw.endswith("s") and lw[:-1] in TYPE_WORDS else lw
        if base in TYPE_WORDS:
            kinds_named = (kinds_named or ()) + TYPE_WORDS[base]
        elif lw and lw not in FILLER:
            words.append(w)
    return words, kinds_named


def _busy() -> None:
    if db.RESTORING.is_set():                            # a backup is being restored right now
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    from .auth import user_dict
    r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if r is None or r["disabled"]:
        return None
    user = user_dict(r)                                    # never an admin's powers: admins get no content access
    user["assistant_ok"] = notify.prefs_of(r).get("assistantOk", True) is not False
    return user


tools = assist_tools.Catalogue(
    "docs", targets=[rf"/doc/{ID}", rf"/folder/{ID}", rf"/file/{ID}"],
    actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers", conn)),
    person_enabled=lambda conn, user: user["assistant_ok"], panel=lambda: app_messages.panel(), busy=_busy)


def _day(ts) -> str | None:
    """A stored UTC time as the household's day (Home Assistant's zone)."""
    dt = config.parse_iso(ts)
    return dt.astimezone(config.ZONE.tz).date().isoformat() if dt else None


def _target(node) -> str:
    if node["kind"] == "folder":
        return f"/folder/{node['id']}"
    return f"/doc/{node['id']}" if kinds.is_document(node["kind"]) else f"/file/{node['id']}"


def _open(ctx, node_id: str, wanted: str = "viewer"):
    """(node, role) as the person may see it; Nack not_found for anything they can't (or that's in Trash)."""
    try:
        node, role = sharing.require(ctx.conn, ctx.user, node_id, wanted)
    except HTTPException as e:
        raise bus.Nack("not_found" if e.status_code == 404 else "not_allowed", "document") from None
    if node["trash_id"] is not None:
        raise bus.Nack("not_found", "document")
    return node, role


def _text(ctx, node) -> str:
    try:
        _root, _real, data, _etag, _st, _sha = documents._read(ctx.conn, node)
    except HTTPException as e:
        raise bus.Nack("not_found" if e.status_code == 404 else "invalid", "too_big" if e.status_code == 413
                       else "document") from None
    try:
        text, _meta = text_fmt.decode(data)
    except text_fmt.NotText:
        raise bus.Nack("invalid", "document") from None
    return text


@tools.tool("docs.search",
            "Finds documents, checklists, sheets, folders and files by name or by the words in them, among what the "
            "person can open. `kind` narrows it; a query of just a type (\"sheets\", \"notes\") lists those, newest "
            "first.",
            args={"query": Arg("string", "words to look for", required=True),
                  "kind": Arg("enum", "only this type", values=KINDS)},
            returns="up to 10 matches: id, name, type, folder, a snippet", examples=("Find the note about the boiler",),
            children=True)
def search(ctx):
    words, kinds_named = _search_words(ctx.args["query"])
    types = KIND_MEANS.get(ctx.args["kind"], (ctx.args["kind"],)) if "kind" in ctx.args else None
    if not words:                                       # "sheets", "my notes": that kind's items, newest first
        types = types or (tuple(dict.fromkeys(kinds_named)) if kinds_named else None)
    rows, more = _run_search(ctx, " ".join(words) if words else "*", types, sort_new=not words)
    if not rows and len(words) > 1:                     # no item has every word: any of them, most words first
        hits: dict[str, tuple[int, int, dict]] = {}
        for w in words:
            for i, r in enumerate(_run_search(ctx, w, types)[0]):
                n, first, _ = hits.get(r["id"], (0, i, r))
                hits[r["id"]] = (n + 1, min(first, i), r)
        ranked = sorted(hits.values(), key=lambda h: (-h[0], h[1]))
        rows, more = [h[2] for h in ranked[:SEARCH_RESULTS]], len(ranked) > SEARCH_RESULTS
    if not rows:
        return ctx.result(f"Nothing in Household Docs matches “{ctx.args['query']}”.",
                          links=[ctx.link("Household Docs")])
    items = [{"id": r["id"], "name": r["name"], "type": r["typeLabel"], "folder": r["location"],
              "snippet": (r.get("snippet") or "")[:300] or None, "modified": _day(r["modified"])} for r in rows]
    text = (f"{len(rows)} match{'es' if len(rows) != 1 else ''} for “{ctx.args['query']}”: "
            + "; ".join(f"{r['name']} ({r['typeLabel'].lower()}, in {r['location']})" for r in rows[:5])
            + ("…" if len(rows) > 5 else "") + ".")
    found = [ctx.link(r["name"], _target(r)) for r in rows[:assist_tools.MAX_LINKS]]
    return ctx.result(text, items=items, links=found, more=more)


def _run_search(ctx, q: str, types, sort_new: bool = False) -> tuple[list[dict], bool]:
    """The Search page's search as the person: (results not in the trash, whether there are more)."""
    raw = {"q": q, "match": "any", "scope": ["all"]}
    if types:
        raw["type"] = list(types)
    if sort_new:
        raw["sort"] = "modified"
    try:
        res = engine.run(ctx.conn, ctx.user, engine.options(raw), limit=SEARCH_RESULTS)
        out = engine.describe(ctx.conn, ctx.user, res, 1)
    except HTTPException:
        raise bus.Nack("invalid", "query") from None
    return [r for r in out["results"] if not r.get("inTrash")], out["more"] or len(out["results"]) >= SEARCH_RESULTS


@tools.tool("docs.read",
            "Reads a note's or checklist's text, or a sheet's cells as \"Tab A1: value\" lines, by its id from "
            "docs.search; up to 4000 characters from `offset`.",
            args={"id": Arg("string", "the document's id", required=True, max_length=64),
                  "offset": Arg("number", "where to start, in characters (default 0)", min=0, max=10_000_000)},
            returns="the text, whether there is more, a link to the document", children=True)
def read(ctx):
    node, _role = _open(ctx, ctx.args["id"])
    if not kinds.is_document(node["kind"]):
        raise bus.Nack("invalid", "not_a_document")
    if node["kind"] == "sheet":
        if kids.is_child(ctx.user):
            raise bus.Nack("not_allowed", "child")
        from . import sheets
        try:
            _root, _real, data, _etag, _st, _sha = documents._read(ctx.conn, node)
            sheet = sheets.parse(data, sheets._ext(node))["sheet"]
        except HTTPException:
            raise bus.Nack("invalid", "document") from None
        lines, refs = M.index_text(sheet)
        text = "\n".join(f"{r.replace(chr(9), ' ')}: {t}" for t, r in zip(lines.split("\n") if lines else [], refs))
    else:
        text = _text(ctx, node)
    start = int(ctx.args.get("offset", 0))
    part = text[start:start + READ_CHARS]
    more = start + READ_CHARS < len(text)
    head = f"{node['name']}" + (f" (from character {start})" if start else "") + ":\n"
    return ctx.result(head + (part or "(nothing more)"), links=[ctx.link(f"{node['name']} in Household Docs",
                                                                         _target(node))],
                      items=[{"id": node["id"], "name": node["name"], "kind": node["kind"], "length": len(text),
                              "next_offset": start + READ_CHARS if more else None}], more=more)


@tools.tool("docs.checklist", "A checklist's items, each ticked or still open, by its id from docs.search.",
            args={"id": Arg("string", "the checklist's id", required=True, max_length=64)},
            returns="items with done or open", children=True)
def checklist(ctx):
    node, _role = _open(ctx, ctx.args["id"])
    if node["kind"] != "checklist":
        raise bus.Nack("invalid", "not_a_checklist")
    try:
        items = checklist_md.parse(_text(ctx, node))
    except ValueError:
        raise bus.Nack("invalid", "not_a_checklist") from None
    rows = [{"item": it.text, "done": bool(it.done), "level": it.level} for it in items]
    open_ = [r["item"] for r in rows if not r["done"]]
    text = (f"{node['name']}: {len(open_)} of {len(rows)} open"
            + (": " + "; ".join(open_[:15]) + ("…" if len(open_) > 15 else "") if open_ else "") + ".")
    return ctx.result(text, items=rows, links=[ctx.link(f"{node['name']} in Household Docs", _target(node))])


@tools.tool("docs.note.create", "Makes a new note in the person's My docs → Inbox (after they confirm it).",
            args={"name": Arg("string", "the note's name", required=True, max_length=120),
                  "text": Arg("string", "what the note says", required=True, max_length=4000)},
            acts=True, returns="the new note with a link")
def note_create(ctx):
    try:
        moving.guard()
        root = docops.my_root(ctx.conn, ctx.user)
        inbox = pins._inbox(ctx.conn, ctx.user, root)
        nid = docops.create(ctx.conn, ctx.user, "note", ctx.args["name"], inbox, text_fmt.encode(ctx.args["text"]))
        node = nodes.get(ctx.conn, nid)
        links.update(ctx.conn, ctx.user, node, ctx.args["text"])
    except HTTPException as e:
        raise bus.Nack("not_allowed" if e.status_code in (403, 409, 423) else "invalid", "note") from None
    return ctx.result(f"Made the note “{node['name']}” in My docs → Inbox.",
                      items=[{"id": nid, "name": node["name"]}],
                      links=[ctx.link(f"{node['name']} in Household Docs", f"/doc/{nid}")])
