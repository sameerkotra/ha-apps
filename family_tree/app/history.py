"""Change history & undo (§7).

Every write goes through a `Batch`: one user action ("Add child") = one batch,
each row written = one `changes` row holding JSON snapshots of the row before
and after. Undo replays a batch backwards, but only if every row it touched is
still exactly as the batch left it (otherwise 409 "undo the later change
first"). Undo is itself a batch, so it can be undone too.
"""
import json
import sqlite3

from . import config, db

# Primary key columns of every table the history covers.
PKS = {
    "people": ("id",), "families": ("id",), "events": ("id",), "media": ("id",),
    "media_links": ("id",), "family_children": ("family_id", "child_id"), "stories": ("id",),
    "kin_terms": ("lang", "kin_key"), "media_regions": ("id",),
    "custom_values": ("id",), "sources": ("id",), "citations": ("id",), "contacts": ("id",),
    "event_tithi": ("event_id",), "tithi_dates": ("event_id", "year"),
}
# Rows that point at a row of the key table (table, column). Undoing a create
# hard-deletes the row, which is refused while anything outside the batch
# still refers to it (instead of letting ON DELETE CASCADE eat later work).
DEPENDENTS = {
    "people": [("events", "person_id"), ("family_children", "child_id"), ("families", "partner1_id"),
               ("families", "partner2_id"), ("media_links", "person_id"), ("stories", "person_id"),
               ("media_regions", "person_id"), ("custom_values", "person_id"), ("citations", "person_id"),
               ("contacts", "person_id"), ("sources", "told_by_person_id")],
    "families": [("events", "family_id"), ("family_children", "family_id"), ("media_links", "family_id"),
                 ("custom_values", "family_id"), ("citations", "family_id")],
    "events": [("media_links", "event_id"), ("citations", "event_id"), ("event_tithi", "event_id")],
    # tithi_dates is a cache of computed dates (plus hand overrides); it goes with its event
    "sources": [("citations", "source_id")],
    "media": [("media_links", "media_id"), ("people", "photo_media_id"), ("media_regions", "media_id"),
              ("sources", "media_id")],
    "media_regions": [("people", "photo_region_id")],
}


class Conflict(Exception):
    """A row changed after the batch being undone (→ HTTP 409)."""


class NotFound(Exception):
    pass


def entity_id(table: str, row: dict) -> str:
    return "|".join(str(row[c]) for c in PKS[table])


def _where(table: str, eid: str) -> tuple[str, list]:
    cols = PKS[table]
    vals = eid.split("|")
    return " AND ".join(f"{c} = ?" for c in cols), vals


def fetch(conn, table: str, eid: str) -> dict | None:
    where, vals = _where(table, eid)
    row = conn.execute(f"SELECT * FROM {table} WHERE {where}", vals).fetchone()
    return dict(row) if row else None


def _norm(d):
    return None if d is None else json.loads(json.dumps(d))


class Batch:
    def __init__(self, conn, user_id: str | None, label: str, undo_of: str | None = None):
        self.conn = conn
        self.user_id = user_id
        self.id = db.new_id()
        self.seq = 0
        self.now = config.now_iso()
        self.people: set[str] = set()
        conn.execute("INSERT INTO batches (id, user_id, label, undo_of, created_at) VALUES (?, ?, ?, ?, ?)",
                     (self.id, user_id, label[:200], undo_of, self.now))
        db.set_setting(conn, "tree_version", self.id)   # invalidates relationship caches (relations.py)

    # ---- recording ----
    def _record(self, table, eid, op, before, after):
        self.seq += 1
        self.conn.execute(
            "INSERT INTO changes (id, batch_id, seq, user_id, entity, entity_id, op, before, after, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.new_id(), self.id, self.seq, self.user_id, table, eid, op,
             None if before is None else json.dumps(before), None if after is None else json.dumps(after), self.now))

    def touch(self, *person_ids):
        for pid in person_ids:
            if pid and pid not in self.people:
                self.people.add(pid)
                self.conn.execute("INSERT OR IGNORE INTO batch_people (batch_id, person_id) VALUES (?, ?)", (self.id, pid))

    # ---- writes ----
    def insert(self, table: str, row: dict, op: str = "create") -> dict:
        cols = list(row.keys())
        self.conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [row[c] for c in cols])
        eid = entity_id(table, row)
        after = fetch(self.conn, table, eid)
        self._record(table, eid, op, None, after)
        return after

    def update(self, table: str, eid: str, fields: dict, op: str = "update") -> dict:
        before = fetch(self.conn, table, eid)
        if before is None:
            raise NotFound(f"{table} {eid} not found")
        fields = dict(fields)
        if "updated_at" in before and "updated_at" not in fields:
            fields["updated_at"] = self.now
        changed = {k: v for k, v in fields.items() if before.get(k) != v}
        if not changed or set(changed) == {"updated_at"}:
            return before                                   # nothing to record
        where, vals = _where(table, eid)
        self.conn.execute(f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in changed)} WHERE {where}",
                          list(changed.values()) + vals)
        after = fetch(self.conn, table, eid)
        self._record(table, eid, op, before, after)
        return after

    def soft_delete(self, table: str, eid: str) -> dict:
        return self.update(table, eid, {"deleted_at": self.now}, op="delete")

    def restore(self, table: str, eid: str) -> dict:
        return self.update(table, eid, {"deleted_at": None}, op="restore")

    def hard_delete(self, table: str, eid: str, op: str = "delete") -> dict | None:
        before = fetch(self.conn, table, eid)
        if before is None:
            return None
        where, vals = _where(table, eid)
        self.conn.execute(f"DELETE FROM {table} WHERE {where}", vals)
        self._record(table, eid, op, before, None)
        return before


def _dependents(conn, table: str, eid: str) -> int:
    n = 0
    for dep_table, col in DEPENDENTS.get(table, []):
        n += conn.execute(f"SELECT COUNT(*) FROM {dep_table} WHERE {col} = ?", (eid,)).fetchone()[0]
    if table == "media_links":
        # a profile photo is always one of the person's own photos: the tag that
        # made it theirs stays while a later change still uses it as their photo
        link = fetch(conn, table, eid)
        if link and link["person_id"]:
            n += conn.execute("SELECT COUNT(*) FROM people WHERE id = ? AND photo_media_id = ?",
                              (link["person_id"], link["media_id"])).fetchone()[0]
    return n


def undo(conn, batch_id: str, user_id: str) -> Batch:
    """Revert `batch_id` in a new batch. Raises NotFound / Conflict."""
    b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    if not b:
        raise NotFound("That change isn't in the history.")
    if b["undone_by"]:
        raise Conflict("That change has already been undone.")
    changes = conn.execute("SELECT * FROM changes WHERE batch_id = ? ORDER BY seq DESC", (batch_id,)).fetchall()
    if not changes:
        raise Conflict("That change has nothing to undo.")
    people = [r["person_id"] for r in conn.execute("SELECT person_id FROM batch_people WHERE batch_id = ?", (batch_id,))]
    nb = Batch(conn, user_id, f"Undo: {b['label']}", undo_of=batch_id)
    cols: dict = {}
    for ch in changes:
        table, eid = ch["entity"], ch["entity_id"]
        before = json.loads(ch["before"]) if ch["before"] else None
        after = json.loads(ch["after"]) if ch["after"] else None
        # snapshots become table/column names in SQL: only ever known ones (a
        # restored backup's history is data like any other)
        if table not in PKS:
            raise Conflict("That change can't be undone.")
        if table not in cols:
            cols[table] = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if before is not None and not set(before) <= cols[table]:
            raise Conflict("That change can't be undone.")
        current = fetch(conn, table, eid)
        if current is not None and after is not None and set(current) - set(after):
            # a column added by a later version (e.g. people.remind) isn't
            # in older snapshots: compare only what the batch knew about
            current = {k: v for k, v in current.items() if k in after}
        if _norm(current) != _norm(after):
            raise Conflict("Something this change touched was edited afterwards — undo the later change first.")
        try:
            if before is None:                       # it was created → remove it
                if _dependents(conn, table, eid):
                    raise Conflict("Later changes still use something this change created — undo those first.")
                nb.hard_delete(table, eid)
            elif after is None:                      # it was removed → put it back
                nb.insert(table, before, op="restore")
            else:
                fields = {k: v for k, v in before.items() if k not in PKS[table]}
                # `fields` includes the old updated_at, so the row ends up exactly as
                # `before` and undoing this undo (redo) compares cleanly.
                nb.update(table, eid, fields, op="restore" if ch["op"] == "delete" else "update")
        except sqlite3.IntegrityError:
            raise Conflict("Something this change touched was replaced or removed afterwards — "
                           "undo the later change first.")
    nb.touch(*people)
    conn.execute("UPDATE batches SET undone_by = ? WHERE id = ?", (nb.id, batch_id))
    return nb
