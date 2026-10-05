"""Who may see and do what (SPEC §3.2, §6).

Roles, highest first: owner (the person whose folder it is), manager, editor, viewer. A person's role on a
node is the highest of: owning its root; every share on the node or any folder above it — to them or to
Everyone ('*'); and (admin shared folders, step 3) the root's access. Folder shares are inherited by
everything inside, now and later, including files added outside the app; a deeper share can add access but
never take inherited access away. Turned-off people have no role anywhere. Admins get no content access as
admins.

Every list and search query joins `access_cte()` so the check happens inside the query; a single node is
checked with `role_of()`. Anything without a role answers 404 (as if it didn't exist).

What each role may do (§6.3):
  viewer   open, search, download, make a copy into My docs, favourite; tick checklist items when the share
           allows it (`viewers_tick`, on by default)
  editor   + edit, create inside, rename, move within the shared tree, delete to Trash (not an item that
           carries shares itself)
  manager  + share with others (viewer/editor) and remove them, move anywhere in the owner's folder,
           delete shared items
  owner    + make managers, transfer ownership, restore from and empty Trash
"""
from fastapi import HTTPException

from . import config, db, settings
from .store import nodes

RANK = {"viewer": 1, "editor": 2, "manager": 3, "owner": 4}
ROLE_OF_RANK = {v: k for k, v in RANK.items()}
EVERYONE = "*"
NOT_FOUND = "Not found — it may have been deleted, or it isn't shared with you."
DENIED = {"viewer": NOT_FOUND, "editor": "You can only view this.", "manager": "Only its owner or a manager can do this.",
          "owner": "Only its owner can do this."}
_ROLE_SQL = "CASE s.role WHEN 'manager' THEN 3 WHEN 'editor' THEN 2 ELSE 1 END"


def at_least(role: str | None, wanted: str) -> bool:
    return role is not None and RANK[role] >= RANK[wanted]


# A parent's view of a child's My docs (§17.20) covers only items made through the app while the person is a child
# (`users.child_since`): what they had before — and anything added outside the app — stays theirs alone.
KID_VISIBLE_SQL = "n.created_by IS NOT NULL AND n.ctime >= cu.child_since"


def kid_visible(conn, child_id: str, node) -> bool:
    """Does a parent's view of this child's My docs reach this item? (KID_VISIBLE_SQL in Python)"""
    u = conn.execute("SELECT is_child, child_since FROM users WHERE id = ?", (child_id,)).fetchone()
    if u is None or not u["is_child"] or not u["child_since"] or node["trash_id"] is not None:
        return False
    return node["created_by"] is not None and (node["ctime"] or "") >= u["child_since"]


def access_cte(user_id: str) -> tuple[str, list]:
    """`WITH RECURSIVE … acc(node_id, rank)`: every node `user_id` may open, with their best role's rank.
    Prepend it to a query and join `acc`. (Live-ness — not gone, not in Trash — is the query's own filter.)
    A child (§17.20) gets no Everyone shares and only the admin shared folders marked for kids; a parent an admin
    named gets Can view on their child's My docs."""
    not_child = "NOT EXISTS (SELECT 1 FROM users ku WHERE ku.id = ? AND ku.is_child = 1)"
    sql = (
        "WITH RECURSIVE share_tree(id, rank) AS ("
        f" SELECT s.node_id, {_ROLE_SQL} FROM shares s JOIN nodes sn ON sn.id = s.node_id AND sn.trash_id IS NULL"
        "  JOIN roots pr ON pr.id = sn.root_id AND pr.kind = 'person'"
        f"  WHERE s.user_id = ? OR (s.user_id = '*' AND {not_child})"
        " UNION ALL SELECT c.id, t.rank FROM nodes c JOIN share_tree t ON c.parent_id = t.id WHERE c.trash_id IS NULL"
        "), acc(node_id, rank) AS ("
        " SELECT node_id, MAX(rank) FROM ("
        "  SELECT n.id AS node_id, 4 AS rank FROM nodes n JOIN roots r ON r.id = n.root_id"
        "   WHERE r.kind = 'person' AND r.user_id = ?"
        "  UNION ALL SELECT id, rank FROM share_tree"
        "  UNION ALL SELECT n.id, CASE ra.mode WHEN 'rw' THEN 2 ELSE 1 END FROM root_access ra"
        "   JOIN roots sr ON sr.id = ra.root_id AND sr.kind = 'shared' AND sr.missing = 0"
        f"   JOIN nodes n ON n.root_id = ra.root_id WHERE ra.user_id IN (?, '*') AND (sr.kids = 1 OR {not_child})"
        "    AND (n.trash_id IS NULL OR ra.mode = 'rw')"
        "  UNION ALL SELECT n.id, 1 FROM kid_parents kp JOIN roots cr ON cr.kind = 'person' AND cr.user_id = kp.child_id"
        "   JOIN users cu ON cu.id = kp.child_id AND cu.is_child = 1 AND cu.child_since IS NOT NULL"
        "   JOIN nodes n ON n.root_id = cr.id WHERE kp.parent_id = ? AND n.trash_id IS NULL"
        f"    AND {KID_VISIBLE_SQL}"
        " ) GROUP BY node_id) ")
    return sql, [user_id, user_id, user_id, user_id, user_id, user_id]


def is_child_id(conn, user_id: str) -> bool:
    r = conn.execute("SELECT is_child FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(r and r["is_child"])


def _child(conn, user: dict) -> bool:
    if "is_child" in user:
        return bool(user["is_child"])
    return is_child_id(conn, user["id"])


def is_parent_of(conn, user_id: str, child_id: str | None) -> bool:
    """An admin let this person view that child's My docs (§17.20) — what was made there while they're a child."""
    if not child_id:
        return False
    return conn.execute("SELECT 1 FROM kid_parents kp JOIN users u ON u.id = kp.child_id AND u.is_child = 1 "
                        "WHERE kp.child_id = ? AND kp.parent_id = ?", (child_id, user_id)).fetchone() is not None


def user_row(conn, user_id: str):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def is_enabled(conn, user_id: str) -> bool:
    u = user_row(conn, user_id)
    return u is not None and not u["disabled"]


def role_of(conn, user: dict, node) -> str | None:
    """The person's best role on a node, or None (no access, or they're turned off)."""
    if node is None or user.get("disabled"):
        return None
    root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
    if root is None:
        return None
    if root["kind"] == "person" and root["user_id"] == user["id"]:
        return "owner"
    if node["trash_id"] is not None:            # in Trash: only the Trash's owner, or Read and write on the folder
        if root["kind"] == "shared" and root_role(conn, user, root) == "editor":
            return "editor"
        return None
    if root["kind"] == "shared":                # access is the folder's (§9.2); a missing folder is hidden
        return root_role(conn, user, root)
    ids = [node["id"]] + [a["id"] for a in nodes.ancestors(conn, node)]
    q = ",".join("?" * len(ids))
    who = ("s.user_id = ?" if _child(conn, user) else "s.user_id IN (?, '*')")     # children: no Everyone (§17.20)
    r = conn.execute(f"SELECT MAX({_ROLE_SQL}) FROM shares s WHERE s.node_id IN ({q}) AND {who}",
                     (*ids, user["id"])).fetchone()
    rank = r[0] or 0
    if rank < RANK["viewer"] and is_parent_of(conn, user["id"], root["user_id"]) \
            and kid_visible(conn, root["user_id"], node):
        rank = RANK["viewer"]                   # a parent viewing their child's My docs (made while a child)
    return ROLE_OF_RANK.get(rank)


def root_role(conn, user: dict, root) -> str | None:
    """A person's role on a root as a whole: owner of their own folder (a parent: viewer of their child's); in an
    admin shared folder viewer (read only) or editor (read and write), from their own row or the Everyone row,
    the better of the two. Children only reach folders marked for kids."""
    if root is None or user.get("disabled"):
        return None
    if root["kind"] == "person":
        if root["user_id"] == user["id"]:
            return "owner"
        return "viewer" if is_parent_of(conn, user["id"], root["user_id"]) else None
    if root["missing"]:
        return None
    if _child(conn, user) and not root["kids"]:
        return None
    r = conn.execute("SELECT MAX(CASE mode WHEN 'rw' THEN 2 ELSE 1 END) FROM root_access WHERE root_id = ? "
                     "AND user_id IN (?, '*')", (root["id"], user["id"])).fetchone()
    return ROLE_OF_RANK.get(r[0] or 0)


def require(conn, user: dict, node_id: str, wanted: str = "viewer"):
    """(node, role) when the person has at least `wanted`; 404 when they can't see it at all, 403 when they can
    see it but not do this."""
    node = nodes.get(conn, node_id)
    role = role_of(conn, user, node)
    if role is None:
        raise HTTPException(404, NOT_FOUND)
    if not at_least(role, wanted):
        raise HTTPException(403, DENIED[wanted])
    return node, role


def viewers_may_tick(conn, user: dict, node) -> bool:
    """A viewer may tick checklist items when a share that reaches them (on the item or a folder above, to them
    or to Everyone) allows it."""
    ids = [node["id"]] + [a["id"] for a in nodes.ancestors(conn, node)]
    q = ",".join("?" * len(ids))
    who = "user_id = ?" if _child(conn, user) else "user_id IN (?, '*')"
    r = conn.execute(f"SELECT 1 FROM shares WHERE node_id IN ({q}) AND {who} AND viewers_tick = 1 LIMIT 1",
                     (*ids, user["id"])).fetchone()
    return r is not None


def shared_ancestor(conn, user: dict, node):
    """The highest item (the node or a folder above it) whose own share gives this non-owner editor or more:
    the top of the shared tree they may move things within."""
    chain = nodes.ancestors(conn, node) + [node]
    who = "s.user_id = ?" if _child(conn, user) else "s.user_id IN (?, '*')"
    for n in chain:
        r = conn.execute(f"SELECT MAX({_ROLE_SQL}) FROM shares s WHERE s.node_id = ? AND {who}",
                         (n["id"], user["id"])).fetchone()
        if (r[0] or 0) >= RANK["editor"]:
            return n
    return None


def has_own_shares(conn, node_id: str) -> bool:
    return conn.execute("SELECT 1 FROM shares WHERE node_id = ? LIMIT 1", (node_id,)).fetchone() is not None


def owner_id(conn, node) -> str | None:
    r = conn.execute("SELECT kind, user_id FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
    return r["user_id"] if r and r["kind"] == "person" else None


def who_can_open(conn, node) -> list[str]:
    """Everyone who can open `node` now (ids, '*' for Everyone): kept with a rename in 🕑 Activity, so the old
    name is shown only to people who could see it then (§17.4)."""
    root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
    if root is None:
        return []
    if root["kind"] == "shared":
        return [r["user_id"] for r in conn.execute("SELECT user_id FROM root_access WHERE root_id = ?", (root["id"],))]
    ids = [node["id"]] + [a["id"] for a in nodes.ancestors(conn, node)]
    q = ",".join("?" * len(ids))
    out = [root["user_id"]] + [r["user_id"] for r in conn.execute(
        f"SELECT DISTINCT user_id FROM shares WHERE node_id IN ({q})", ids)]
    if kid_visible(conn, root["user_id"], node):
        out += [r["parent_id"] for r in conn.execute("SELECT parent_id FROM kid_parents WHERE child_id = ?",
                                                     (root["user_id"],))]
    return list(dict.fromkeys(out))


def could_open(conn, viewer: dict, acl) -> bool:
    """Was the viewer among `acl` (who_can_open at the time)?"""
    if not isinstance(acl, list):
        return False
    return viewer["id"] in acl or (EVERYONE in acl and not _child(conn, viewer))


# ---------- what a viewer may learn about the folders above an item ----------
def visible_ancestors(conn, viewer: dict, node) -> list:
    """The folders above `node` (top first) that the viewer can open themselves: the trailing run of the chain,
    since access is inherited downwards. The owner (and, in an admin shared folder, anyone with access) gets all."""
    chain = nodes.ancestors(conn, node)
    root = conn.execute("SELECT kind, user_id FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
    if root is None or root["kind"] == "shared" or root["user_id"] == viewer["id"]:
        return chain
    out = []
    for a in reversed(chain):
        if role_of(conn, viewer, a) is None:
            break
        out.append(a)
    out.reverse()
    return out


def visible_path(conn, viewer: dict, node) -> str:
    """The item's path as the viewer may see it: in full for its owner and in admin shared folders; otherwise
    only the folders they can open themselves, the rest as "…" ("… / Trip / Letter.txt")."""
    root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
    if root is None or root["kind"] == "shared" or root["user_id"] == viewer["id"]:
        return nodes.display_path(conn, node)
    above = visible_ancestors(conn, viewer, node)
    return " / ".join(["…"] + [a["name"] for a in above] + [node["name"]])


# ---------- shares ----------
def shares_json(conn, node, viewer: dict) -> dict:
    """The item's own shares and those it inherits from folders above (names and roles only)."""
    own = []
    for s in conn.execute("SELECT s.*, u.name FROM shares s LEFT JOIN users u ON u.id = s.user_id WHERE s.node_id = ? "
                          "ORDER BY s.user_id = '*' DESC, u.name COLLATE NOCASE", (node["id"],)):
        own.append({"userId": s["user_id"], "name": "Everyone" if s["user_id"] == EVERYONE else (s["name"] or "Someone"),
                    "role": s["role"], "viewersTick": bool(s["viewers_tick"]), "addedAt": s["added_at"]})
    inherited = []
    for a in visible_ancestors(conn, viewer, node):     # folders the viewer can't open aren't named (nor their shares)
        for s in conn.execute("SELECT s.*, u.name FROM shares s LEFT JOIN users u ON u.id = s.user_id WHERE s.node_id = ?",
                              (a["id"],)):
            inherited.append({"userId": s["user_id"], "role": s["role"], "from": a["name"], "fromId": a["id"],
                              "name": "Everyone" if s["user_id"] == EVERYONE else (s["name"] or "Someone")})
    oid = owner_id(conn, node)
    o = user_row(conn, oid) if oid else None
    return {"owner": {"id": oid, "name": o["name"] if o else None}, "shares": own, "inherited": inherited,
            "everyoneAllowed": bool(settings.get("everyone_shares", conn)),
            "everyoneDefaultRole": settings.get("everyone_default_role", conn)}


def set_share(conn, actor: dict, actor_role: str, node, target: str, role: str, viewers_tick: bool | None) -> bool:
    """Add or change one share. Returns True when it is new (the person is then told)."""
    if role not in ("viewer", "editor", "manager"):
        raise HTTPException(422, "The role is viewer, editor or manager.")
    if not at_least(actor_role, "manager"):
        raise HTTPException(403, "Only its owner or a manager can share this.")
    if role == "manager" and actor_role != "owner":
        raise HTTPException(403, "Only the owner can make someone a manager.")
    oid = owner_id(conn, node)
    if oid is None:
        raise HTTPException(409, "Items in admin shared folders are shared through the folder's access, not one by one.")
    if target == EVERYONE:
        if not settings.get("everyone_shares", conn):
            raise HTTPException(403, "Sharing with Everyone is turned off on this Home Assistant.")
        if role == "manager":
            raise HTTPException(422, "Everyone can be given Can view or Can edit.")
    else:
        u = user_row(conn, target)
        if u is None:
            raise HTTPException(404, "That person isn't known to Household Docs.")
        if target == oid:
            raise HTTPException(422, "That's the owner — they have every right already.")
        if u["disabled"]:
            raise HTTPException(409, f"{u['name']} can't use Household Docs (an admin has turned it off for them).")
    cur = conn.execute("SELECT * FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], target)).fetchone()
    if cur is not None and cur["role"] == "manager" and actor_role != "owner":
        raise HTTPException(403, "Only the owner can change a manager.")
    tick = 1 if (viewers_tick if viewers_tick is not None else (bool(cur["viewers_tick"]) if cur else True)) else 0
    if cur is None:
        conn.execute("INSERT INTO shares (node_id, user_id, role, viewers_tick, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (node["id"], target, role, tick, actor["id"], config.now_iso()))
        db.audit(conn, "share_added", actor["id"], node["id"], node["root_id"])
        from . import activity
        activity.record(conn, "shared", actor["id"], node, target_user=target)
        return True
    conn.execute("UPDATE shares SET role = ?, viewers_tick = ? WHERE node_id = ? AND user_id = ?",
                 (role, tick, node["id"], target))
    db.audit(conn, "share_changed", actor["id"], node["id"], node["root_id"])
    return False


def remove_share(conn, actor: dict, actor_role: str | None, node, target: str) -> None:
    cur = conn.execute("SELECT * FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], target)).fetchone()
    if cur is None:
        raise HTTPException(404, "That share isn't there.")
    if target != actor["id"]:
        if not at_least(actor_role, "manager"):
            raise HTTPException(403, "Only its owner or a manager can remove people.")
        if cur["role"] == "manager" and actor_role != "owner":
            raise HTTPException(403, "Only the owner can remove a manager.")
    conn.execute("DELETE FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], target))
    db.audit(conn, "share_removed", actor["id"], node["id"], node["root_id"])


def leave(conn, user: dict, node) -> None:
    """Leave an item shared with you directly (an Everyone share can only be hidden)."""
    cur = conn.execute("SELECT 1 FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], user["id"])).fetchone()
    if cur is None:
        raise HTTPException(409, "This isn't shared with you directly (it comes from Everyone or a folder above) — "
                                 "you can hide it instead.")
    conn.execute("DELETE FROM shares WHERE node_id = ? AND user_id = ?", (node["id"], user["id"]))
    conn.execute("DELETE FROM user_state WHERE user_id = ? AND node_id = ? AND favourite = 0", (user["id"], node["id"]))
    db.audit(conn, "share_left", user["id"], node["id"], node["root_id"])


def set_hidden(conn, user: dict, node, hidden: bool) -> None:
    conn.execute("INSERT INTO user_state (user_id, node_id, hidden) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id, node_id) DO UPDATE SET hidden = excluded.hidden", (user["id"], node["id"], 1 if hidden else 0))


# ---------- the spaces ----------
def _top_shared(conn, user: dict, who: str, show_hidden: bool):
    """Items with a share to `who` (a person id or '*') whose folders above have no such share (the top of each
    shared tree), not owned by `user`, live, and — unless show_hidden — not hidden by `user`."""
    rows = conn.execute(
        "SELECT n.*, s.role AS share_role, s.added_at AS shared_at, s.added_by AS shared_by, r.user_id AS owner_id, "
        "  COALESCE(us.hidden, 0) AS hidden, COALESCE(us.favourite, 0) AS favourite "
        "FROM shares s JOIN nodes n ON n.id = s.node_id JOIN roots r ON r.id = n.root_id "
        "LEFT JOIN user_state us ON us.user_id = ? AND us.node_id = n.id "
        f"WHERE s.user_id = ? AND {nodes.LIVE} AND r.kind = 'person' AND r.user_id != ? "
        "ORDER BY s.added_at DESC", (user["id"], who, user["id"])).fetchall()
    out = []
    for r in rows:
        if r["hidden"] and not show_hidden:
            continue
        above = [a["id"] for a in nodes.ancestors(conn, r)]
        if above:
            q = ",".join("?" * len(above))
            if conn.execute(f"SELECT 1 FROM shares WHERE node_id IN ({q}) AND user_id = ? LIMIT 1", (*above, who)).fetchone():
                continue
        out.append(r)
    return out


def shared_with_me(conn, user: dict, show_hidden: bool = False):
    return _top_shared(conn, user, user["id"], show_hidden)


def everyone_items(conn, user: dict, show_hidden: bool = False):
    """Everything shared with Everyone — other people's (and, marked as yours, your own). Children: nothing
    (§17.20)."""
    if _child(conn, user):
        return []
    others = _top_shared(conn, user, EVERYONE, show_hidden)
    mine = conn.execute(
        "SELECT n.*, s.role AS share_role, s.added_at AS shared_at, s.added_by AS shared_by, r.user_id AS owner_id, "
        "  0 AS hidden, COALESCE(us.favourite, 0) AS favourite "
        "FROM shares s JOIN nodes n ON n.id = s.node_id JOIN roots r ON r.id = n.root_id "
        "LEFT JOIN user_state us ON us.user_id = ? AND us.node_id = n.id "
        f"WHERE s.user_id = '*' AND {nodes.LIVE} AND r.kind = 'person' AND r.user_id = ? ORDER BY s.added_at DESC",
        (user["id"], user["id"])).fetchall()
    return others + list(mine)
