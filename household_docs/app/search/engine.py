"""The Search page (SPEC §10): one search over everything the person can open — My docs, Shared with me, Everyone
and their admin shared folders — with scopes, name modes, content search, patterns, filters, sorting, grouping
and paging.

How a search runs:
1. `options()` reads the request (the box `q` plus the page's options and filters); `parse.parse` reads the box's
   syntax. Both end up as one set of filters; every filter in use comes back as a chip.
2. One SQL query, with the person's access (sharing.access_cte) joined in, selects what matches the scopes, the
   filters and the words (names with LIKE / GLOB over the folded name, text with FTS5 — or LIKE without it).
   Nothing is filtered afterwards in the browser.
3. Regular expressions (name or text) don't run here: the query's results are the candidates, which
   search/regex_worker.py matches in its own process with a 5-second limit.
4. Up to MAX_RESULTS results; one page of PAGE_SIZE is described (location, highlights, the line to open at).

Extension point: `FILTERS[key] = fn(ctx, value, neg) -> (sql, params)` adds a filter; a filter nobody registered
shows as a chip saying it isn't available yet and doesn't narrow anything. Tags (`tag:`, `-tag:`) and colours
(`color:`) are built in (tags.py, step 7).
"""
import json
import time

from fastapi import HTTPException

from .. import db, settings, sharing
from ..store import fileio, kinds, nodes, paths, roots
from . import filters as flt, fts, parse, pattern, regex_worker

MAX_RESULTS = 500
PAGE_SIZE = 50
NAME_CANDIDATES = 200_000
CONTENT_CANDIDATES = 5_000
SCOPES = ("all", "mine", "shared", "everyone", "folders")
NAME_MODES = ("contains", "starts", "exact", "ends", "wildcard", "regex")
SORTS = ("relevance", "name", "modified", "created", "size", "type", "location")
GROUPS = ("none", "location", "type", "modified")
MATCHES = ("any", "name", "content")
IS_VALUES = {"fav": "Favourites", "favourite": "Favourites", "shared": "Shared", "open": "Checklists with open items",
             "done": "Checklists all done", "empty": "Empty folders", "dup": "Duplicates",
             "new": "Changed since you looked"}
SHARED_VALUES = {"byme": "Shared by me", "withme": "Shared with me", "not": "Not shared", "everyone": "Shared with Everyone"}
OPTION_KEYS = ("match", "nameMode", "contentMode", "scope", "sub", "trash", "modified", "created", "type", "ext",
               "size", "by", "owner", "shared", "access", "is", "path", "tag", "color", "sort", "dir", "group")
LIST_KEYS = ("scope", "type", "ext", "is", "tag")
FILTERS: dict = {}                     # extension point: key -> fn(ctx, value, neg) -> (sql, params)


class SearchError(HTTPException):
    def __init__(self, message: str, status: int = 422):
        super().__init__(status, message)


# ---------------------------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------------------------
def options(raw: dict) -> dict:
    """The request's options, cleaned: every key known, lists split on commas, unknown values dropped."""
    o = {"q": str(raw.get("q") or "")[:400]}
    for k in OPTION_KEYS:
        v = raw.get(k)
        if v is None or v == "":
            continue
        if k in LIST_KEYS:
            items = v if isinstance(v, list) else str(v).split(",")
            items = [str(x).strip()[:80] for x in items if str(x).strip()][:20]
            if items:
                o[k] = items
        elif k in ("sub", "trash"):
            o[k] = str(v).lower() in ("1", "true", "yes", "on")
        else:
            o[k] = str(v).strip()[:120]
    if o.get("match") not in MATCHES:
        o["match"] = "any"
    if o.get("nameMode") not in NAME_MODES:
        o["nameMode"] = "contains"
    if o.get("contentMode") not in ("words", "regex"):
        o["contentMode"] = "words"
    if o.get("sort") not in SORTS:
        o["sort"] = "relevance"
    if o.get("dir") not in ("asc", "desc"):
        o["dir"] = "asc" if o["sort"] in ("name", "type", "location") else "desc"
    if o.get("group") not in GROUPS:
        o["group"] = "none"
    o.setdefault("sub", True)
    return o


def saved_filters(o: dict) -> dict:
    """What a saved / recent search keeps besides the box (the page's options and filters)."""
    return {k: o[k] for k in OPTION_KEYS if k in o and k not in ("sort", "dir", "group")
            and not (k == "match" and o[k] == "any") and not (k == "nameMode" and o[k] == "contains")
            and not (k == "contentMode" and o[k] == "words") and not (k == "sub" and o[k] is True)}


# ---------------------------------------------------------------------------------------------
# the query
# ---------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, conn, user: dict, o: dict):
        self.conn, self.user, self.o = conn, user, o
        self.where: list[str] = []
        self.params: list = []
        self.ctes: list[str] = []
        self.cte_params: list = []
        self.chips: list[dict] = []
        self.notices: list[str] = []
        self.name_rx: list[str] = []         # name regular expressions (all must match)
        self.content_rx: str | None = None
        self.name_hl: list[dict] = []        # what to highlight in names
        self.words: list[str] = []           # positive words (snippets, open-at-line)
        self.has_criteria = False
        self.content_cte = False
        self.match_name_sql: str | None = None
        self.match_name_params: list = []
        self.name_rx_any: str | None = None    # a regex for "the name or the text"
        self.stopped = False
        self.rx_spans: dict = {}
        self.rx_found: dict = {}
        self.path_likes: list[str] = []      # path: filters, checked again on what the person may see (run)

    def add(self, sql: str, *params) -> None:
        self.where.append(sql)
        self.params.extend(params)
        self.has_criteria = True

    def chip(self, key: str, label: str, *, token: str | None = None, param: str | None = None, value=None,
             error: bool = False) -> None:
        c = {"key": key, "label": label}
        if token is not None:
            c["token"] = token
        if param is not None:
            c["param"] = param
        if value is not None:
            c["value"] = value
        if error:
            c["error"] = True
        self.chips.append(c)


def _cte_tree(ctx: Ctx, name: str, seed_sql: str, *params) -> None:
    """A recursive CTE `name(id)`: the seed nodes and everything inside them."""
    if any(c.startswith(name + "(") for c in ctx.ctes):
        return
    ctx.ctes.append(f"{name}(id) AS ({seed_sql} UNION SELECT c.id FROM nodes c JOIN {name} t ON c.parent_id = t.id)")
    ctx.cte_params.extend(params)


def _person(conn, value: str, me: dict) -> str | None:
    v = (value or "").strip()
    if not v:
        return None
    if v.lower() == "me":
        return me["id"]
    r = conn.execute("SELECT id FROM users WHERE id = ? OR username = ? COLLATE NOCASE OR name = ? COLLATE NOCASE "
                     "ORDER BY disabled LIMIT 1", (v, v, v)).fetchone()
    if r is None:
        r = conn.execute("SELECT id FROM users WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE ORDER BY disabled, name LIMIT 1",
                         (fts.like_escape(v) + "%",)).fetchone()
    return r["id"] if r else None


def _person_name(conn, uid: str) -> str:
    r = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
    return r["name"] if r else "someone"


def _my_root_id(ctx: Ctx) -> str:
    r = roots.person_root(ctx.conn, ctx.user["id"])
    return r["id"] if r else ""


# ---------- scopes ----------
def _scope(ctx: Ctx, values: list[str], token: str | None = None, param: bool = True) -> None:
    conds, params = [], []
    for v in values:
        lv = v.lower()
        if lv in ("all", "everywhere", ""):
            continue
        if lv in ("mine", "my", "mydocs"):
            conds.append("(r.kind = 'person' AND r.user_id = ?)")
            params.append(ctx.user["id"])
            label = "in My docs"
        elif lv in ("shared", "withme"):
            _cte_tree(ctx, "sw", "SELECT node_id FROM shares WHERE user_id = ?", ctx.user["id"])
            conds.append("(r.kind = 'person' AND r.user_id != ? AND n.id IN (SELECT id FROM sw))")
            params.append(ctx.user["id"])
            label = "in Shared with me"
        elif lv == "everyone":
            _cte_tree(ctx, "se", "SELECT node_id FROM shares WHERE user_id = '*'")
            conds.append("(r.kind = 'person' AND n.id IN (SELECT id FROM se))")
            label = "in Everyone"
        elif lv in ("folders", "sharedfolders"):
            conds.append("r.kind = 'shared'")
            label = "in Shared folders"
        elif lv == "trash":
            ctx.o["trash"] = True
            conds.append("n.trash_id IS NOT NULL")
            label = "in Trash"
        elif lv.startswith("root:"):
            rid = v[5:]
            root = roots.root_row(ctx.conn, rid)
            if root is None or sharing.root_role(ctx.conn, ctx.user, root) is None:
                ctx.chip("in", "a shared folder you can't open", token=token, param="scope" if param else None,
                         value=v, error=True)
                conds.append("0")
                continue
            conds.append("n.root_id = ?")
            params.append(rid)
            label = f"in {root['label'] if root['kind'] == 'shared' else 'My docs'}"
        elif lv.startswith("folder:"):
            fid = v[7:]
            node = nodes.get(ctx.conn, fid)
            if node is None or node["kind"] != "folder" or sharing.role_of(ctx.conn, ctx.user, node) is None:
                ctx.chip("in", "a folder you can't open", token=token, param="scope" if param else None, value=v,
                         error=True)
                conds.append("0")
                continue
            if ctx.o.get("sub", True):
                conds.append("(n.root_id = ? AND substr(n.rel, 1, ?) = ?)")
                params += [node["root_id"], len(node["rel"]) + 1, node["rel"] + "/"]
                label = f"in {node['name']} and its folders"
            else:
                conds.append("n.parent_id = ?")
                params.append(node["id"])
                label = f"in {node['name']}"
        else:
            root = ctx.conn.execute("SELECT * FROM roots WHERE kind = 'shared' AND label = ? COLLATE NOCASE", (v,)).fetchone()
            if root is None or sharing.root_role(ctx.conn, ctx.user, root) is None:
                ctx.chip("in", f"no shared folder “{v}”", token=token, param="scope" if param else None, value=v,
                         error=True)
                conds.append("0")
                continue
            conds.append("n.root_id = ?")
            params.append(root["id"])
            label = f"in {root['label']}"
        ctx.chip("in", label, token=token, param="scope" if param else None, value=v)
    if conds:
        ctx.add("(" + " OR ".join(conds) + ")", *params)


# ---------- filters ----------
def _f_dates(ctx: Ctx, col: str, key: str, value: str, token, param) -> None:
    try:
        lo, hi = flt.date_range(value)
    except flt.FilterError as e:
        ctx.chip(key, str(e), token=token, param=param, error=True)
        ctx.add("0")
        return
    if lo:
        ctx.add(f"{col} >= ?", lo)
    if hi:
        ctx.add(f"{col} < ?", hi)
    ctx.chip(key, ("Modified " if key == "modified" else "Created ") + flt.date_label(value), token=token, param=param)


def _f_size(ctx: Ctx, value: str, token, param) -> None:
    try:
        lo, hi = flt.parse_size(value)
    except flt.FilterError as e:
        ctx.chip("size", str(e), token=token, param=param, error=True)
        ctx.add("0")
        return
    ctx.add("n.kind != 'folder' AND n.size IS NOT NULL")
    if lo is not None:
        ctx.add("n.size >= ?", lo)
    if hi is not None:
        ctx.add("n.size < ?", hi)
    ctx.chip("size", "Size " + flt.size_label(value), token=token, param=param)


def _f_types(ctx: Ctx, values: list[str], neg: bool, token, param) -> None:
    conds, params, labels = [], [], []
    for v in values:
        try:
            t = flt.type_name(v)
        except flt.FilterError as e:
            ctx.chip("type", str(e), token=token, param=param, value=v, error=True)
            ctx.add("0")
            return
        labels.append(flt.TYPE_LABELS[t])
        if t == "other":
            q = ",".join("?" * len(flt.OTHER_EXTS))
            conds.append(f"(n.kind = 'file' AND (n.ext IS NULL OR n.ext NOT IN ({q})))")
            params += list(flt.OTHER_EXTS)
            continue
        spec = flt.TYPE_GROUPS[t]
        if "kinds" in spec:
            conds.append(f"n.kind IN ({','.join('?' * len(spec['kinds']))})")
            params += list(spec["kinds"])
        else:
            conds.append(f"(n.kind != 'folder' AND n.ext IN ({','.join('?' * len(spec['exts']))}))")
            params += list(spec["exts"])
    if conds:
        sql = "(" + " OR ".join(conds) + ")"
        ctx.add(f"NOT {sql}" if neg else sql, *params)
        ctx.chip("type", ("Not " if neg else "Type ") + " or ".join(labels), token=token, param=param)


def _f_ext(ctx: Ctx, values: list[str], neg: bool, token, param) -> None:
    exts = []
    for v in values:
        for part in v.split(","):
            try:
                exts.append(flt.clean_ext(part))
            except flt.FilterError as e:
                ctx.chip("ext", str(e), token=token, param=param, error=True)
                ctx.add("0")
                return
    if exts:
        q = ",".join("?" * len(exts))
        ctx.add(f"(n.ext IS NULL OR n.ext NOT IN ({q}))" if neg else f"n.ext IN ({q})", *exts)
        ctx.chip("ext", ("Not ." if neg else ".") + ", .".join(exts), token=token, param=param)


def _f_by(ctx: Ctx, value: str, token, param) -> None:
    v = value.strip().lower()
    if v in ("outside", "nobody"):
        ctx.add("n.updated_by IS NULL")
        ctx.chip("by", "Changed outside the app", token=token, param=param)
        return
    if v in ("notme", "not-me", "others"):
        ctx.add("(n.updated_by IS NULL OR n.updated_by != ?)", ctx.user["id"])
        ctx.chip("by", "Not changed by me", token=token, param=param)
        return
    uid = _person(ctx.conn, value, ctx.user)
    if uid is None:
        ctx.chip("by", f"Nobody called “{value}”", token=token, param=param, error=True)
        ctx.add("0")
        return
    ctx.add("n.updated_by = ?", uid)
    ctx.chip("by", "Changed by " + ("me" if uid == ctx.user["id"] else _person_name(ctx.conn, uid)), token=token, param=param)


def _f_owner(ctx: Ctx, value: str, token, param) -> None:
    uid = _person(ctx.conn, value, ctx.user)
    if uid is None:
        ctx.chip("owner", f"Nobody called “{value}”", token=token, param=param, error=True)
        ctx.add("0")
        return
    ctx.add("r.kind = 'person' AND r.user_id = ?", uid)
    ctx.chip("owner", "Owned by " + ("me" if uid == ctx.user["id"] else _person_name(ctx.conn, uid)), token=token,
             param=param)


def _f_shared(ctx: Ctx, value: str, token, param) -> None:
    v = value.strip().lower().replace("-", "").replace("_", "")
    me = ctx.user["id"]
    if v == "byme":
        _cte_tree(ctx, "sb", "SELECT s.node_id FROM shares s JOIN nodes x ON x.id = s.node_id JOIN roots xr ON "
                             "xr.id = x.root_id WHERE xr.kind = 'person' AND xr.user_id = ?", me)
        ctx.add("r.kind = 'person' AND r.user_id = ? AND n.id IN (SELECT id FROM sb)", me)
    elif v == "withme":
        _cte_tree(ctx, "sw", "SELECT node_id FROM shares WHERE user_id = ?", me)
        ctx.add("r.kind = 'person' AND r.user_id != ? AND n.id IN (SELECT id FROM sw)", me)
    elif v in ("everyone", "witheveryone"):
        _cte_tree(ctx, "se", "SELECT node_id FROM shares WHERE user_id = '*'")
        ctx.add("r.kind = 'person' AND n.id IN (SELECT id FROM se)")
    elif v in ("not", "no", "none", "notshared"):
        _cte_tree(ctx, "sa", "SELECT DISTINCT node_id FROM shares")
        ctx.add("r.kind = 'person' AND n.id NOT IN (SELECT id FROM sa)")
    else:
        ctx.chip("shared", "Shared is byme, withme, not or everyone", token=token, param=param, error=True)
        ctx.add("0")
        return
    ctx.chip("shared", SHARED_VALUES["everyone" if v == "witheveryone" else ("not" if v in ("no", "none", "notshared") else v)],
             token=token, param=param)


def _f_access(ctx: Ctx, value: str, token, param) -> None:
    v = value.strip().lower()
    if v in ("edit", "write", "rw", "canedit"):
        ctx.add("acc.rank >= 2")
        ctx.chip("access", "I can edit", token=token, param=param)
    elif v in ("read", "view", "ro", "readonly"):
        ctx.add("acc.rank = 1")
        ctx.chip("access", "Read only", token=token, param=param)
    else:
        ctx.chip("access", "Access is edit or read", token=token, param=param, error=True)
        ctx.add("0")


def _f_is(ctx: Ctx, values: list[str], neg: bool, token, param) -> None:
    for v in values:
        lv = v.strip().lower()
        if lv in ("fav", "favourite", "favorite", "star"):
            sql, ps = "n.id IN (SELECT node_id FROM user_state WHERE user_id = ? AND favourite = 1)", [ctx.user["id"]]
            lv = "fav"
        elif lv == "shared":
            _cte_tree(ctx, "sa", "SELECT DISTINCT node_id FROM shares")
            sql, ps = "(r.kind = 'shared' OR n.id IN (SELECT id FROM sa))", []
        elif lv == "open":
            sql, ps = "(n.kind = 'checklist' AND n.check_open > 0)", []
        elif lv == "done":
            sql, ps = "(n.kind = 'checklist' AND n.check_open = 0 AND n.check_done > 0)", []
        elif lv == "empty":
            sql, ps = ("(n.kind = 'folder' AND NOT EXISTS (SELECT 1 FROM nodes c WHERE c.parent_id = n.id "
                       "AND c.gone_at IS NULL AND c.trash_id IS NULL))"), []
        elif lv in ("dup", "duplicate", "duplicates"):
            sql, ps = ("(n.kind != 'folder' AND n.sha256 IS NOT NULL AND n.size > 0 AND EXISTS (SELECT 1 FROM nodes d "
                       "JOIN acc a2 ON a2.node_id = d.id WHERE d.sha256 = n.sha256 AND d.id != n.id "
                       "AND d.gone_at IS NULL AND d.trash_id IS NULL))"), []
            lv = "dup"
        elif lv in ("new", "changed", "unseen"):                 # §17.21: changed since you last looked
            from .. import changes
            sql, ps = changes.changed_sql(ctx.user["id"])
            sql = f"({sql})"
            lv = "new"
        else:
            ctx.chip("is", f"Unknown is:{v} — try fav, shared, open, done, empty, dup or new", token=token, param=param,
                     value=v, error=True)
            ctx.add("0")
            continue
        ctx.add(f"NOT {sql}" if neg else sql, *ps)
        ctx.chip("is", ("Not: " if neg else "") + IS_VALUES[lv], token=token, param=param, value=v)


def _f_path(ctx: Ctx, value: str, token, param) -> None:
    if not value.strip("/ "):
        return
    # the folders an item is in: "/<root name>/<folders>/" (the item's own name isn't part of its path)
    like = pattern.path_like(value)
    ctx.add("('/' || r.label || '/' || substr(n.rel, 1, length(n.rel) - length(n.name))) LIKE ? ESCAPE '\\'", like)
    ctx.path_likes.append(like)
    ctx.chip("path", f"Path {value}", token=token, param=param)


def _f_color(ctx: Ctx, value: str, token, param) -> None:
    from .. import tags
    v = value.strip().lower()
    if v not in tags.COLOURS:
        ctx.chip("color", f"Unknown colour {value} — try " + ", ".join(tags.COLOURS), token=token, param=param,
                 value=value, error=True)
        ctx.add("0")
        return
    ctx.add("n.color = ?", v)
    ctx.chip("color", f"Colour {v}", token=token, param=param)


def _f_tags(ctx: Ctx, values: list[str], neg: bool, token, param) -> None:
    """tag:taxes / -tag:taxes (§17.1): items carrying (or not carrying) the tag."""
    from .. import tags
    for v in values:
        if not v.strip().lstrip("#").strip():
            continue
        sql, ps = tags.tag_sql(v)
        ctx.add(f"NOT {sql}" if neg else sql, *ps)
        ctx.chip("tag", ("Not tagged " if neg else "Tag ") + v.strip(), token=token, param=param, value=v)


def _filter(ctx: Ctx, key: str, value, neg: bool = False, token: str | None = None, from_param: bool = False) -> None:
    param = key if from_param else None
    values = value if isinstance(value, list) else [value]
    if key in FILTERS:
        for v in values:
            sql, ps = FILTERS[key](ctx, v, neg)
            if sql:
                ctx.add(sql, *ps)
        return
    if key in ("modified", "created"):
        _f_dates(ctx, "n.mtime" if key == "modified" else "n.ctime", key, values[0], token, param)
    elif key == "size":
        _f_size(ctx, values[0], token, param)
    elif key == "type":
        _f_types(ctx, values, neg, token, param)
    elif key == "ext":
        _f_ext(ctx, values, neg, token, param)
    elif key == "by":
        _f_by(ctx, values[0], token, param)
    elif key == "owner":
        _f_owner(ctx, values[0], token, param)
    elif key == "shared":
        _f_shared(ctx, values[0], token, param)
    elif key == "access":
        _f_access(ctx, values[0], token, param)
    elif key == "is":
        _f_is(ctx, values, neg, token, param)
    elif key == "path":
        _f_path(ctx, values[0], token, param)
    elif key == "color":
        _f_color(ctx, values[0], token, param)
    elif key == "tag":
        _f_tags(ctx, values, neg, token, param)
    elif key == "in":
        _scope(ctx, values, token, param=from_param)
    else:                                   # anything else not registered yet
        ctx.chip(key, f"{key}: isn't available yet", token=token, param=param, error=True)


# ---------- words ----------
def _fts_term(t: dict) -> str:
    v = t["v"].replace('"', " ").strip()
    if t["t"] == "phrase":
        return '"' + v + '"'
    return " AND ".join('"' + w + '"*' for w in fts.words(v)) or '""'


def _fts_expr(clauses: list[list[dict]]) -> str:
    parts = []
    for c in clauses:
        alts = [_fts_term(t) for t in c]
        parts.append(alts[0] if len(alts) == 1 else "(" + " OR ".join(alts) + ")")
    return " AND ".join(parts)


def _name_term(t: dict) -> tuple[str, list]:
    """A term over the folded name: a phrase as it is, a word (or several, from punctuation) each somewhere."""
    if t["t"] == "phrase":
        return "n.name_folded LIKE ? ESCAPE '\\'", ["%" + fts.like_escape(fts.fold(t["v"])) + "%"]
    ws = [fts.fold(w) for w in (fts.words(t["v"]) or [t["v"]])]
    return " AND ".join("n.name_folded LIKE ? ESCAPE '\\'" for _ in ws), ["%" + fts.like_escape(w) + "%" for w in ws]


def _name_clauses(clauses) -> tuple[str, list]:
    parts, params = [], []
    for c in clauses:
        alts = []
        for t in c:
            s, p = _name_term(t)
            alts.append("(" + s + ")")
            params += p
        parts.append("(" + " OR ".join(alts) + ")")
    return " AND ".join(parts), params


def _content_like(clauses) -> tuple[str, list]:
    parts, params = [], []
    for c in clauses:
        alts = []
        for t in c:
            ws = [t["v"]] if t["t"] == "phrase" else (fts.words(t["v"]) or [t["v"]])
            alts.append("(" + " AND ".join("f.body LIKE ? ESCAPE '\\'" for _ in ws) + ")")
            params += ["%" + fts.like_escape(w) + "%" for w in ws]
        parts.append("(" + " OR ".join(alts) + ")")
    return " AND ".join(parts), params


def _excludes(ctx: Ctx, excludes: list[dict], match: str) -> None:
    use_fts = db.has_fts(ctx.conn)
    for t in excludes:
        side = t.get("side", "any")
        name_side = side in ("any", "name") and match != "content"
        content_side = side in ("any", "content") and match != "name"
        if name_side:
            s, p = _name_term(t)
            ctx.add(f"NOT ({s})", *p)
        if content_side:
            if use_fts:
                ctx.add("n.id NOT IN (SELECT node_id FROM fts WHERE body MATCH ?)", _fts_term(t))
            else:
                ctx.add("n.id NOT IN (SELECT node_id FROM fts f WHERE f.body LIKE ? ESCAPE '\\')",
                        "%" + fts.like_escape(t["v"]) + "%")


def _words(ctx: Ctx, p: parse.Parsed) -> None:
    """The free text, by the name mode and what to match (name or content)."""
    o = ctx.o
    mode, match = o["nameMode"], o["match"]
    text = p.text.strip()
    name_rx = mode == "regex" and match != "content"
    content_rx = o["contentMode"] == "regex" and match != "name"
    if (name_rx or content_rx) and text:
        err = pattern.check_regex(text)
        if err:
            raise SearchError(err)
        if name_rx and content_rx:
            ctx.name_rx_any, ctx.content_rx = text, text           # the name or the text
        elif name_rx:
            ctx.name_rx.append(text)
        else:
            ctx.content_rx = text
        ctx.has_criteria = True
        return
    if not p.clauses:
        return
    ctx.words = [t["v"] for t in p.terms()]
    ctx.has_criteria = True
    name_sql, name_params = None, []
    if mode == "contains":
        name_sql, name_params = _name_clauses(p.clauses)
        ctx.name_hl.append({"mode": "words", "v": [fts.fold(w) for t in p.terms() for w in (fts.words(t["v"]) or [t["v"]])]})
    elif mode in ("starts", "exact", "ends"):
        f = fts.fold(text)
        e = fts.like_escape(f)
        stem = "CASE WHEN n.ext IS NOT NULL THEN substr(n.name_folded, 1, length(n.name_folded) - length(n.ext) - 1) ELSE n.name_folded END"
        if mode == "starts":
            name_sql, name_params = "n.name_folded LIKE ? ESCAPE '\\'", [e + "%"]
        elif mode == "exact":
            name_sql, name_params = f"(n.name_folded = ? OR {stem} = ?)", [f, f]
        else:
            name_sql, name_params = f"(n.name_folded LIKE ? ESCAPE '\\' OR {stem} LIKE ? ESCAPE '\\')", ["%" + e, "%" + e]
        ctx.name_hl.append({"mode": mode, "v": f})
        match = "name" if match == "any" else match
    elif mode == "wildcard":
        name_sql, name_params = "n.name_folded GLOB ?", [pattern.wildcard_glob(text)]
        ctx.name_hl.append({"mode": "wildcard", "v": text})
        match = "name" if match == "any" else match
    elif mode == "regex":                     # (with "text only": the words are searched in the text)
        name_sql, name_params = "0", []
        match = "content"
    if match == "name":
        ctx.add("(" + name_sql + ")", *name_params)
        return
    use_fts = db.has_fts(ctx.conn)
    ctx.content_cte = True
    if use_fts:
        ctx.ctes.append("ch(id, snip, score) AS (SELECT node_id, snippet(fts, 2, '⁅', '⁆', ' … ', 12), bm25(fts) "
                        "FROM fts WHERE body MATCH ?)")
        ctx.cte_params.append(_fts_expr(p.clauses))
    else:
        csql, cparams = _content_like(p.clauses)
        ctx.ctes.append(f"ch(id, snip, score) AS (SELECT f.node_id, substr(f.body, 1, 200), 0 FROM fts f WHERE {csql})")
        ctx.cte_params.extend(cparams)
    if match == "content":
        ctx.add("ch.id IS NOT NULL")
    else:
        ctx.add(f"(({name_sql}) OR ch.id IS NOT NULL)", *name_params)
        ctx.match_name_sql, ctx.match_name_params = name_sql, name_params


def _patterns(ctx: Ctx, p: parse.Parsed) -> None:
    """name:/content: parts of the box — always required, whatever the match setting."""
    use_fts = db.has_fts(ctx.conn)
    for pt in p.patterns:
        side, mode, v, tok = pt["side"], pt["mode"], pt["v"], pt["token"]
        ctx.has_criteria = True
        if mode == "regex":
            if not settings.get("regex_search", ctx.conn):
                raise SearchError("Regular-expression search is turned off on this Home Assistant (App settings).", 403)
            if ctx.user.get("is_child"):
                raise SearchError("Regular-expression search isn't available in Kids' space.", 403)
            err = pattern.check_regex(v)
            if err:
                raise SearchError(err)
            if side == "name":
                ctx.name_rx.append(v)
                ctx.chip("name", f"name matches /{v}/", token=tok)
            else:
                if ctx.content_rx and ctx.content_rx != v:
                    raise SearchError("One text pattern at a time.")
                ctx.content_rx = v
                ctx.chip("content", f"text matches /{v}/", token=tok)
        elif mode == "wildcard":
            ctx.add("n.name_folded GLOB ?", pattern.wildcard_glob(v))
            ctx.name_hl.append({"mode": "wildcard", "v": v})
            ctx.chip("name", f"name like {v}", token=tok)
        elif side == "name":
            s, ps = _name_clauses([[{"t": "word", "v": w}] for w in v.split()])
            ctx.add(s, *ps)
            ctx.name_hl.append({"mode": "words", "v": [fts.fold(w) for w in fts.words(v)]})
            ctx.chip("name", f"“{v}” in the name", token=tok)
        else:
            clauses = [[{"t": "word", "v": w}] for w in v.split()]
            if use_fts:
                ctx.add("n.id IN (SELECT node_id FROM fts WHERE body MATCH ?)", _fts_expr(clauses))
            else:
                s, ps = _content_like(clauses)
                ctx.add(f"n.id IN (SELECT f.node_id FROM fts f WHERE {s})", *ps)
            ctx.words += v.split()
            ctx.chip("content", f"“{v}” in the text", token=tok)


def _order(ctx: Ctx) -> str:
    o = ctx.o
    d = "DESC" if o["dir"] == "desc" else "ASC"
    sort = o["sort"]
    if sort == "relevance":
        if ctx.content_cte and ctx.match_name_sql:
            return "by_name DESC, score, n.name_folded"
        if ctx.content_cte:
            return "score, n.name_folded"
        return "n.kind != 'folder', n.name_folded"
    cols = {"name": f"n.name_folded {d}", "modified": f"n.mtime {d}, n.name_folded",
            "created": f"n.ctime {d}, n.name_folded", "size": f"COALESCE(n.size, -1) {d}, n.name_folded",
            "type": f"n.kind {d}, n.ext {d}, n.name_folded", "location": f"r.label {d}, n.rel {d}"}
    return cols[sort]


def build(conn, user: dict, o: dict) -> Ctx:
    ctx = Ctx(conn, user, o)
    p = parse.parse(o["q"], raw_text=o["nameMode"] not in ("contains",) or o["contentMode"] == "regex")
    for c in p.chips:
        ctx.chips.append(c)
    if o.get("nameMode") == "regex" or o.get("contentMode") == "regex":
        if not settings.get("regex_search", conn):
            raise SearchError("Regular-expression search is turned off on this Home Assistant (App settings).", 403)
        if user.get("is_child"):
            raise SearchError("Regular-expression search isn't available in Kids' space.", 403)
    _words(ctx, p)
    _patterns(ctx, p)
    _excludes(ctx, p.excludes, o["match"])
    if p.excludes:
        ctx.has_criteria = True
    # the page's options
    if o.get("scope"):
        _scope(ctx, o["scope"], param=True)
    for key in ("modified", "created", "type", "ext", "size", "by", "owner", "shared", "access", "is", "path", "tag",
                "color"):
        if o.get(key):
            _filter(ctx, key, o[key], from_param=True)
    # the box's filters
    for f in p.filters:
        _filter(ctx, f["key"], f["value"], f["neg"], token=f["token"])
    if o.get("trash"):
        ctx.chip("trash", "including Trash", param="trash")
    return ctx


def _sql(ctx: Ctx, select: str, limit: int, select_params=()) -> tuple[str, list]:
    acc_sql, acc_params = sharing.access_cte(ctx.user["id"])
    sql = acc_sql
    params = list(acc_params)
    if ctx.ctes:
        sql = sql.rstrip() + ", " + ", ".join(ctx.ctes) + " "
        params += ctx.cte_params
    params += list(select_params)
    live = "n.gone_at IS NULL" + ("" if ctx.o.get("trash") else " AND n.trash_id IS NULL")
    where = [live, "(r.kind = 'person' OR r.missing = 0)"] + ctx.where
    join_ch = " LEFT JOIN ch ON ch.id = n.id" if ctx.content_cte else ""
    sql += (f"SELECT {select} FROM nodes n JOIN acc ON acc.node_id = n.id JOIN roots r ON r.id = n.root_id{join_ch} "
            f"WHERE {' AND '.join('(' + w + ')' for w in where)} ORDER BY {_order(ctx)} LIMIT ?")
    params += ctx.params + [limit]
    return sql, params


def _select(ctx: Ctx) -> str:
    cols = "n.*, acc.rank AS acc_rank, r.kind AS root_kind, r.label AS root_label, r.user_id AS root_owner"
    if ctx.content_cte:
        name = ctx.match_name_sql
        by_name = f"CASE WHEN {name} THEN 1 ELSE 0 END" if name else "0"
        cols += f", {by_name} AS by_name, ch.snip AS snip, ch.score AS score"
    else:
        cols += ", 1 AS by_name, NULL AS snip, 0 AS score"
    return cols


# ---------------------------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------------------------
def _path_visible_ok(conn, user: dict, row, likes: list[str]) -> bool:
    """A `path:` filter on someone else's item matches only the folders the person can open (the SQL matched the
    whole path; a hidden folder's name must never decide a result — security review 2026-10)."""
    if row["root_kind"] == "shared" or row["root_owner"] == user["id"]:
        return True
    parts = [p for p in location(conn, row, user) if p != "…"]
    path = "/" + "/".join(parts) + "/"
    return all(conn.execute("SELECT ? LIKE ? ESCAPE '\\'", (path, lk)).fetchone()[0] for lk in likes)


def run(conn, user: dict, o: dict, *, limit: int = MAX_RESULTS) -> dict:
    """{rows, total, more, ms, chips, notices, stopped, ctx} — rows in order, at most `limit`."""
    t0 = time.monotonic()
    ctx = build(conn, user, o)
    out = {"chips": ctx.chips, "notices": ctx.notices, "stopped": False, "rows": [], "total": 0, "more": False}
    if not ctx.has_criteria:
        out["ms"] = int((time.monotonic() - t0) * 1000)
        out["ctx"] = ctx
        return out
    rx_needed = bool(ctx.name_rx or ctx.content_rx or ctx.name_rx_any)
    if not rx_needed:
        sel_params = ctx.match_name_params if ctx.content_cte and ctx.match_name_sql else []
        sql, params = _sql(ctx, _select(ctx), limit + 1, sel_params)
        rows = conn.execute(sql, params).fetchall()
    else:
        rows = _run_regex(ctx, limit)
        out["stopped"] = ctx.stopped
    if ctx.path_likes:
        rows = [r for r in rows if _path_visible_ok(conn, user, r, ctx.path_likes)]
    out["more"] = len(rows) > limit
    out["rows"] = rows[:limit]
    out["total"] = len(out["rows"])
    out["ms"] = int((time.monotonic() - t0) * 1000)
    out["ctx"] = ctx
    return out


def _run_regex(ctx: Ctx, limit: int) -> list:
    """Candidates from SQL (scopes, filters, words, access), then the worker. Name patterns must all match;
    with "name or text" a match in either counts."""
    conn = ctx.conn
    ctx.stopped = False
    name_any = ctx.name_rx_any
    content_rx = ctx.content_rx
    with regex_worker.busy(ctx.user["id"]):
        if content_rx and not name_any:          # only files whose text can match: narrow them in SQL
            limit_b = _content_limit(conn)
            docs = [k.id for k in kinds.all_kinds() if k.document]
            texty = sorted(kinds.TEXT_EXTS)
            ctx.add(f"n.kind != 'folder' AND COALESCE(n.size, 0) <= ? AND (n.kind IN ({','.join('?' * len(docs))}) "
                    f"OR n.ext IN ({','.join('?' * len(texty))}))", limit_b, *docs, *texty)
            for lit in [x for x in pattern.required_literals(content_rx) if x.isascii()]:
                ctx.add("n.id IN (SELECT f.node_id FROM fts f WHERE f.body LIKE ? ESCAPE '\\')",
                        "%" + fts.like_escape(lit) + "%")
        sql, params = _sql(ctx, "n.id, n.name, n.kind, n.ext, n.size, n.root_id, n.rel", NAME_CANDIDATES + 1)
        cands = conn.execute(sql, params).fetchall()
        if len(cands) > NAME_CANDIDATES:
            ctx.notices.append(f"Only the first {NAME_CANDIDATES:,} items were looked at — narrow it down.")
            cands = cands[:NAME_CANDIDATES]
        order = {r["id"]: i for i, r in enumerate(cands)}
        spans: dict = {}
        keep = [r["id"] for r in cands]
        for rx in ctx.name_rx:                                  # every name pattern must match
            res = regex_worker.run(rx, "name", [(i, cands[order[i]]["name"]) for i in keep], max_matches=10 ** 9)
            if res.error:
                raise SearchError(res.error)
            ctx.stopped |= res.stopped
            hit = {m["id"]: (m["s"], m["e"]) for m in res.matches}
            keep = [i for i in keep if i in hit]
            for i in keep:
                spans.setdefault(i, hit[i])
        found: dict = {i: {} for i in keep} if not (content_rx or name_any) else {}
        if name_any and keep:                                     # name regex OR text regex (match any)
            res = regex_worker.run(name_any, "name", [(i, cands[order[i]]["name"]) for i in keep], max_matches=limit + 1)
            if res.error:
                raise SearchError(res.error)
            ctx.stopped |= res.stopped
            for m in res.matches:
                found[m["id"]] = {}
                spans.setdefault(m["id"], (m["s"], m["e"]))
        if content_rx and keep:
            files = _content_candidates(ctx, [cands[order[i]] for i in keep if i not in found])
            res = regex_worker.run(content_rx, "content", files, limit_bytes=_content_limit(conn),
                                   max_matches=limit + 1)
            if res.error:
                raise SearchError(res.error)
            ctx.stopped |= res.stopped
            for m in res.matches:
                found[m["id"]] = {"line": m.get("line"), "snip": m.get("snip")}
        ids = sorted(found, key=lambda i: order[i])[:limit + 1]
    ctx.rx_spans = spans
    ctx.rx_found = found
    if not ids:
        return []
    rows = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        acc_sql, acc_params = sharing.access_cte(ctx.user["id"])
        rows += conn.execute(acc_sql + "SELECT n.*, acc.rank AS acc_rank, r.kind AS root_kind, r.label AS root_label, "
                             "r.user_id AS root_owner, 1 AS by_name, NULL AS snip, 0 AS score FROM nodes n "
                             f"JOIN acc ON acc.node_id = n.id JOIN roots r ON r.id = n.root_id WHERE n.id IN ({q})",
                             (*acc_params, *chunk)).fetchall()
    rows.sort(key=lambda r: order[r["id"]])
    return rows


def _content_limit(conn) -> int:
    return int(settings.get("content_index_mb", conn)) * 1024 * 1024


def _content_candidates(ctx: Ctx, rows) -> list:
    """Files whose text a content pattern is matched against: text files and documents within content_index_mb,
    narrowed by the literal text every match must contain; each path realpath-checked inside its root."""
    conn = ctx.conn
    limit = _content_limit(conn)
    lits = [x for x in pattern.required_literals(ctx.content_rx) if x.isascii()]
    texty = set(kinds.TEXT_EXTS)
    out = []
    root_cache: dict = {}
    pre = None
    if lits:
        ids = [r["id"] for r in rows if r["kind"] != "folder"]
        pre = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ",".join("?" * len(chunk))
            cond = " AND ".join("f.body LIKE ? ESCAPE '\\'" for _ in lits)
            pre |= {x[0] for x in conn.execute(f"SELECT f.node_id FROM fts f WHERE f.node_id IN ({q}) AND {cond}",
                                               (*chunk, *["%" + fts.like_escape(s) + "%" for s in lits]))}
    for r in rows:
        if r["kind"] == "folder" or (r["size"] or 0) > limit or limit <= 0:
            continue
        if not (kinds.is_document(r["kind"]) or (r["ext"] or "") in texty):
            continue
        if pre is not None and r["id"] not in pre:
            continue
        try:
            if r["root_id"] not in root_cache:
                root_cache[r["root_id"]] = roots.root_real(roots.root_row(conn, r["root_id"]))
            real = paths.resolve(root_cache[r["root_id"]], r["rel"])
        except Exception:
            continue
        out.append((r["id"], real))
        if len(out) >= CONTENT_CANDIDATES:
            ctx.notices.append(f"Only the first {CONTENT_CANDIDATES:,} files' text was searched — narrow it down.")
            break
    return out


# ---------------------------------------------------------------------------------------------
# describing results
# ---------------------------------------------------------------------------------------------
def location(conn, row, user: dict) -> list[str]:
    """["House papers", "Taxes", "2026"] — where a result is (for your own things "My docs"; in someone else's
    folder only the folders you can open, ["…", "Evidence"])."""
    if row["trash_id"]:
        return ["Trash"]
    if row["root_kind"] == "shared":
        first = row["root_label"]
    elif row["root_owner"] == user["id"]:
        first = "My docs"
    else:
        # someone else's folder: only the folders above that this person can open themselves — the rest is "…"
        # (security review 2026-10; like sharing.visible_path and the crumbs)
        chain = nodes.ancestors(conn, row)
        seen = []
        for anc in reversed(chain):
            if sharing.role_of(conn, user, anc) is None:
                break
            seen.append(anc["name"])
        seen.reverse()
        if len(seen) < len(chain):
            return ["…"] + seen
        first = row["root_label"]
        return [first] + seen
    return [first] + [a["name"] for a in nodes.ancestors(conn, row)]


def _mark(name: str, spans: list[tuple[int, int]]) -> str | None:
    spans = sorted(s for s in spans if 0 <= s[0] < s[1] <= len(name))
    if not spans:
        return None
    out, at = [], 0
    for s, e in spans:
        if s < at:
            continue
        out.append(name[at:s] + "⁅" + name[s:e] + "⁆")
        at = e
    out.append(name[at:])
    return "".join(out)


def name_marks(ctx: Ctx, row) -> str | None:
    """The name with its matching part(s) wrapped in ⁅ ⁆ (plain text; the browser never parses it as HTML)."""
    import re
    name = row["name"]
    rx = ctx.rx_spans.get(row["id"])
    if rx:
        return _mark(name, [rx])
    folded, idx = fts.fold_map(name)
    spans = []
    for hl in ctx.name_hl:
        if hl["mode"] == "words":
            for w in hl["v"]:
                if not w:
                    continue
                at = folded.find(w)
                if at >= 0:
                    spans.append((idx[at], idx[at + len(w) - 1] + 1))
        elif hl["mode"] == "starts" and folded.startswith(hl["v"]) and hl["v"]:
            spans.append((0, idx[len(hl["v"]) - 1] + 1))
        elif hl["mode"] == "ends" and hl["v"]:
            at = folded.rfind(hl["v"])
            if at >= 0:
                spans.append((idx[at], idx[at + len(hl["v"]) - 1] + 1))
        elif hl["mode"] in ("exact", "wildcard"):
            if hl["mode"] == "exact" or re.match(pattern.wildcard_to_regex(hl["v"]), name, re.I):
                spans.append((0, len(name)))
    return _mark(name, spans)


def _mark_words(text: str, words: list[str]) -> str:
    """⁅ ⁆ around each word found (as the FTS snippets do)."""
    folded, idx = fts.fold_map(text)
    spans = []
    for w in words:
        fw = fts.fold(w)
        if not fw:
            continue
        at = folded.find(fw)
        if at >= 0:
            spans.append((idx[at], idx[at + len(fw) - 1] + 1))
    return _mark(text, spans) or text


def at_line(conn, row, words: list[str]) -> int | None:
    """The first line of a note or checklist that holds one of the words (to open it there)."""
    if row["kind"] not in ("note", "markdown", "checklist") or not words:
        return None
    try:
        real = roots.root_real(roots.root_row(conn, row["root_id"]))
        path = paths.resolve(real, row["rel"])
        if (row["size"] or 0) > _content_limit(conn):
            return None
        with fileio.open_read(path) as f:
            text = f.read(_content_limit(conn)).decode("utf-8", "replace")
    except Exception:
        return None
    folded = [fts.fold(w) for w in words if w]
    for n, line in enumerate(text.split("\n"), 1):
        fl = fts.fold(line)
        if any(w and w in fl for w in folded):
            return n
    return None


def at_cell(conn, row, words: list[str]) -> dict | None:
    """The first cell of a sheet (any tab) that shows one of the words: {tab, ref, text} — from the indexed
    text and `nodes.sheet_cells` (which cell each line came from), so the file isn't read again."""
    if row["kind"] != "sheet" or not words:
        return None
    r = conn.execute("SELECT n.sheet_cells, f.body FROM nodes n JOIN fts f ON f.node_id = n.id WHERE n.id = ?",
                     (row["id"],)).fetchone()
    if not r or not r["sheet_cells"] or not r["body"]:
        return None
    try:
        refs = json.loads(r["sheet_cells"])
    except ValueError:
        return None
    folded = [fts.fold(w) for w in words if w]
    for n, line in enumerate(r["body"].split("\n")):
        if n >= len(refs):
            break
        fl = fts.fold(line)
        if any(w and w in fl for w in folded):
            tab, _sep, ref = refs[n].partition("\t")
            return {"tab": tab, "ref": ref, "text": line[:200]}
    return None


def group_of(ctx: Ctx, row, loc: list[str], bounds) -> tuple:
    g = ctx.o["group"]
    if g == "location":
        label = " › ".join(loc)
        return (label.casefold(),), label
    if g == "type":
        label = flt.type_label_of(row["kind"], row["ext"])
        return (label,), label
    if g == "modified":
        today, week = bounds
        m = row["mtime"] or ""
        if m >= today:
            return (0,), "Today"
        if m >= week:
            return (1,), "This week"
        return (2,), "Earlier"
    return (0,), None


def describe(conn, user: dict, res: dict, page: int) -> dict:
    """The response for one page: results with location, highlights and where to open, plus count and paging."""
    ctx = res["ctx"]
    rows = res["rows"]
    bounds = flt.today_bounds()
    locs = {}
    if ctx.o["group"] != "none":
        keyed = []
        for i, r in enumerate(rows):
            loc = location(conn, r, user)
            locs[r["id"]] = loc
            gk, _label = group_of(ctx, r, loc, bounds)
            keyed.append((gk, i, r))
        keyed.sort(key=lambda x: (x[0], x[1]))
        rows = [x[2] for x in keyed]
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    part = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    names = {}
    ids = [r["updated_by"] for r in part if r["updated_by"]] + [r["root_owner"] for r in part if r["root_owner"]]
    if ids:
        q = ",".join("?" * len(set(ids)))
        names = {x["id"]: x["name"] for x in conn.execute(f"SELECT id, name FROM users WHERE id IN ({q})", list(set(ids)))}
    favs = set()
    if part:
        q = ",".join("?" * len(part))
        favs = {x[0] for x in conn.execute(f"SELECT node_id FROM user_state WHERE user_id = ? AND favourite = 1 AND "
                                           f"node_id IN ({q})", (user["id"], *[r["id"] for r in part]))}
    rinfo = nodes.roots_info(conn)
    found = ctx.rx_found
    from .. import changes as changes_mod, tags as tags_mod
    tag_map = tags_mod.for_ids(conn, [r["id"] for r in part])
    changed = changes_mod.row_extras(conn, user, [r for r in part if not r["trash_id"]])     # §17.21: the dots
    results = []
    for r in part:
        loc = locs.get(r["id"]) or location(conn, r, user)
        _gk, glabel = group_of(ctx, r, loc, bounds)
        extra = {"location": " › ".join(loc), "locationParts": loc, "rootKind": r["root_kind"],
                 "rootLabel": r["root_label"] if r["root_kind"] == "shared" else None,
                 "parentRef": nodes.parent_ref(r, rinfo), "created": r["ctime"],
                 "updatedByName": names.get(r["updated_by"]), "ownerId": r["root_owner"] if r["root_kind"] == "person" else None,
                 "ownerName": names.get(r["root_owner"]) if r["root_kind"] == "person" else None,
                 "favourite": r["id"] in favs, "typeLabel": flt.type_label_of(r["kind"], r["ext"]),
                 "nameMarked": name_marks(ctx, r), "matchedName": bool(r["by_name"]), "inTrash": bool(r["trash_id"]),
                 "trashId": r["trash_id"], "group": glabel, "tags": tag_map.get(r["id"], []),
                 "changed": r["id"] in changed}
        f = found.get(r["id"]) or {}
        snip = f.get("snip") or r["snip"]
        extra["snippet"] = " ".join(str(snip).split()) if snip else None
        line = f.get("line") or (at_line(conn, r, ctx.words) if ctx.words and not r["trash_id"] else None)
        extra["line"] = line
        if r["kind"] == "sheet" and ctx.words and not r["trash_id"]:
            cell = at_cell(conn, r, ctx.words)
            if cell:
                stem = r["name"].rsplit(".", 1)[0]
                extra["cell"] = {"tab": cell["tab"], "ref": cell["ref"]}
                extra["cellLabel"] = f"{stem} › {cell['tab']} › {cell['ref']}"
                extra["snippet"] = f"{extra['cellLabel']}: " + _mark_words(cell["text"], ctx.words)
        if r["kind"] == "file" and (r["ext"] or "").lower() == "pdf" and ctx.words and not r["trash_id"]:
            from .. import pdftext                 # §17.5: which page the words are on
            pg = pdftext.page_of(conn, r, ctx.words)
            if pg:
                extra["page"] = pg
                extra["snippet"] = f"page {pg}: " + (extra["snippet"] or "")
        results.append(nodes.node_json(r, role=sharing.ROLE_OF_RANK.get(r["acc_rank"]), extra=extra))
    return {"results": results, "total": len(rows), "more": res["more"], "page": page, "pages": pages,
            "pageSize": PAGE_SIZE, "ms": res["ms"], "chips": res["chips"], "notices": res["notices"],
            "stopped": res["stopped"], "searched": ctx.has_criteria}


def export_rows(conn, user: dict, res: dict):
    """The result list for CSV: names, locations, dates, sizes — never any content."""
    for r in res["rows"]:
        loc = location(conn, r, user)
        yield r, " › ".join(loc)
