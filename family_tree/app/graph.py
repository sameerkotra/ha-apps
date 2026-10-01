"""The whole tree as an in-memory graph, cached until the next write.

Every history batch writes a new `tree_version` setting in the same
transaction, so a cached graph is reused exactly as long as nothing changed.
20 000 people load in well under a second; most requests hit the cache.
"""
from collections import deque
import threading
from dataclasses import dataclass, field

from . import config, dates, db, names

_lock = threading.Lock()
_cache: dict = {"version": None, "graph": None}


@dataclass
class Person:
    id: str
    given: str | None
    surname: str | None
    birth_surname: str | None
    nickname: str | None
    gender: str
    deceased: bool
    photo: str | None
    remind: bool = False                                # 🔔 reminders for this person (§9)
    photo_region: str | None = None                     # the profile photo is this box of it (§13.2)
    given_local: str | None = None                      # names in Telugu / Hindi script (§13.7)
    surname_local: str | None = None
    name_order: str | None = None                       # None = the name_order App setting
    card_fields: list = field(default_factory=list)     # custom fields shown on cards (§13.8): ["Gotram: X"]
    birth: dict | None = None
    death: dict | None = None
    parent_fams: list = field(default_factory=list)    # [(family_id, relation)]
    partner_fams: list = field(default_factory=list)   # [family_id]

    @property
    def name(self) -> str:
        return names.full(self.given, self.surname, self.nickname, self.name_order)

    @property
    def local_name(self) -> str | None:
        return names.full(self.given_local, self.surname_local, None, self.name_order, fallback=None)


@dataclass
class Family:
    id: str
    p1: str | None
    p2: str | None
    kind: str
    ended: str | None
    children: list = field(default_factory=list)       # [(child_id, relation, position)]
    marriage: dict | None = None

    def partners(self) -> list:
        return [p for p in (self.p1, self.p2) if p]

    def other(self, pid) -> str | None:
        return self.p2 if self.p1 == pid else self.p1 if self.p2 == pid else None


class Graph:
    def __init__(self, people: dict, families: dict):
        self.people = people
        self.families = families

    # ---- neighbours ----
    def parents(self, pid) -> list:
        """[(parent_id, family_id, relation)]; birth families first."""
        out = []
        p = self.people.get(pid)
        if not p:
            return out
        for fid, rel in sorted(p.parent_fams, key=lambda x: x[1] != "birth"):
            fam = self.families[fid]
            for par in fam.partners():
                out.append((par, fid, rel))
        return out

    def children(self, pid) -> list:
        out = []
        p = self.people.get(pid)
        if not p:
            return out
        for fid in p.partner_fams:
            for cid, rel, _pos in self.families[fid].children:
                out.append((cid, fid, rel))
        return out

    def partners(self, pid) -> list:
        """[(partner_id or None, family_id)]"""
        p = self.people.get(pid)
        return [(self.families[f].other(pid), f) for f in p.partner_fams] if p else []

    def siblings(self, pid) -> list:
        """[(sibling_id, full: bool)] — full when they share a family."""
        p = self.people.get(pid)
        if not p:
            return []
        my_fams = {f for f, _ in p.parent_fams}
        my_parents = {par for par, _, _ in self.parents(pid)}
        seen, out = set(), []
        for fid in my_fams:
            for cid, _rel, _pos in self.families[fid].children:
                if cid != pid and cid not in seen:
                    seen.add(cid)
                    out.append((cid, True))
        for par in my_parents:                          # half-siblings through another family
            for cid, _fid, _rel in self.children(par):
                if cid != pid and cid not in seen:
                    seen.add(cid)
                    out.append((cid, False))
        return out

    def within(self, start, steps: int) -> set:
        """Everyone within `steps` links of start (start included), where a
        parent, child, sibling or partner each count as one step."""
        seen = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            if seen[cur] >= steps:
                continue
            nbrs = [p for p, _f, _r in self.parents(cur)] + [c for c, _f, _r in self.children(cur)]
            nbrs += [s for s, _full in self.siblings(cur)] + [p for p, _f in self.partners(cur) if p]
            for n in nbrs:
                if n not in seen:
                    seen[n] = seen[cur] + 1
                    q.append(n)
        return set(seen)

    def ancestors(self, pid, max_gen: int = 12) -> dict:
        """{ancestor_id: [path from pid to ancestor]} (pid itself at depth 0), BFS so paths are shortest."""
        paths = {pid: [pid]}
        frontier = [pid]
        for _ in range(max_gen):
            nxt = []
            for cur in frontier:
                for par, _fid, _rel in self.parents(cur):
                    if par not in paths:
                        paths[par] = paths[cur] + [par]
                        nxt.append(par)
            if not nxt:
                break
            frontier = nxt
        return paths

    def is_descendant(self, pid, of_id, limit: int = 200) -> bool:
        """True if pid is of_id or one of of_id's descendants."""
        if pid == of_id:
            return True
        seen, stack = {of_id}, [of_id]
        while stack:
            cur = stack.pop()
            for cid, _f, _r in self.children(cur):
                if cid == pid:
                    return True
                if cid not in seen:
                    seen.add(cid)
                    stack.append(cid)
        return False

    def in_loop(self, pid) -> bool:
        """pid is their own ancestor (only possible through data imported/edited around the checks)."""
        seen, stack = set(), [par for par, _, _ in self.parents(pid)]
        while stack:
            cur = stack.pop()
            if cur == pid:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(par for par, _, _ in self.parents(cur))
        return False


def _event_dict(r) -> dict:
    return {k: r[k] for k in ("id", "date_text", "date_y", "date_m", "date_d", "date_approx", "sort_key", "place", "time")}


def _load(conn) -> Graph:
    people = {}
    for r in conn.execute("SELECT id, given_names, surname, birth_surname, nickname, gender, deceased, photo_media_id, "
                          "remind, photo_region_id, given_local, surname_local, name_order FROM people WHERE deleted_at IS NULL"):
        people[r["id"]] = Person(r["id"], r["given_names"], r["surname"], r["birth_surname"], r["nickname"],
                                 r["gender"], bool(r["deceased"]), r["photo_media_id"], bool(r["remind"]),
                                 r["photo_region_id"] if r["photo_media_id"] else None,
                                 r["given_local"], r["surname_local"], r["name_order"])
    for r in conn.execute("SELECT v.person_id, f.label, v.value FROM custom_values v JOIN custom_fields f ON f.id = v.field_id "
                          "WHERE f.on_card = 1 AND f.archived = 0 AND v.person_id IS NOT NULL ORDER BY f.position, f.label"):
        p = people.get(r["person_id"])
        if p:
            p.card_fields.append(f"{r['label']}: {r['value']}")
    for r in conn.execute("SELECT * FROM events WHERE person_id IS NOT NULL AND type IN ('birth','death')"):
        p = people.get(r["person_id"])
        if p:
            setattr(p, r["type"], _event_dict(r))
    families = {}
    for r in conn.execute("SELECT * FROM families WHERE deleted_at IS NULL"):
        p1 = r["partner1_id"] if r["partner1_id"] in people else None
        p2 = r["partner2_id"] if r["partner2_id"] in people else None
        families[r["id"]] = Family(r["id"], p1, p2, r["kind"], r["ended"])
        for par in (p1, p2):
            if par:
                people[par].partner_fams.append(r["id"])
    for r in conn.execute("SELECT * FROM events WHERE family_id IS NOT NULL AND type = 'marriage'"):
        f = families.get(r["family_id"])
        if f:
            f.marriage = _event_dict(r)
    for r in conn.execute("SELECT * FROM family_children ORDER BY family_id, position"):
        f = families.get(r["family_id"])
        c = people.get(r["child_id"])
        if f and c:
            f.children.append((r["child_id"], r["relation"], r["position"]))
            c.parent_fams.append((r["family_id"], r["relation"]))
    # children in birth order where known, keeping manual order otherwise
    for f in families.values():
        f.children.sort(key=lambda c: (c[2],))
    return Graph(people, families)


def get(conn=None) -> Graph:
    def _get(c):
        version = db.get_setting(c, "tree_version", "0")
        with _lock:
            if _cache["version"] == version and _cache["graph"] is not None:
                return _cache["graph"]
        g = _load(c)
        with _lock:
            _cache["version"], _cache["graph"] = version, g
        return g
    if conn is not None:
        return _get(conn)
    with db.get_conn() as c:
        return _get(c)


def load_fresh(conn) -> Graph:
    """Uncached — for checks inside a write transaction that already changed rows."""
    return _load(conn)


def living(p: Person) -> bool:
    return dates.is_living(p.deceased, p.birth, p.death, config.today())


def summary(p: Person) -> dict:
    """What a tree card / list row needs."""
    from . import features
    alive = living(p)
    script = features.on("script_names")          # switched-off modules are hidden, not deleted
    return {
        "id": p.id, "name": p.name, "given": p.given, "surname": p.surname, "gender": p.gender,
        "living": alive, "years": dates.years_label(p.birth, p.death, alive),
        "photo": p.photo, "photoRegion": p.photo_region, "remind": p.remind and features.on("reminders"),
        "nameLocal": p.local_name if script else None, "givenLocal": p.given_local if script else None,
        "surnameLocal": p.surname_local if script else None,
        "surnameFirst": names.order(p.name_order) == "surname_first",
        "cardFields": p.card_fields if features.on("custom_fields") else [],
    }


def generation_rows(order, neighbours) -> tuple[dict, dict]:
    """Row (generation) and connected group for everyone in `order`: parents
    one row up, children one down, partners on the same row.
    `neighbours(pid)` gives [(other_id, row_step)] with row_step -1/0/+1.
    Where the data disagrees (someone married a cousin's child) the first
    assignment wins; each group's top row is 0. Used by the whole-tree chart
    and the exported website."""
    gen, comp, n = {}, {}, 0
    for start in order:
        if start in gen:
            continue
        gen[start], comp[start] = 0, n
        members, q = [start], deque([start])
        while q:
            cur = q.popleft()
            for other, d in neighbours(cur):
                if other not in gen:
                    gen[other], comp[other] = gen[cur] + d, n
                    members.append(other)
                    q.append(other)
        low = min(gen[m] for m in members)
        for m in members:
            gen[m] -= low
        n += 1
    return gen, comp
