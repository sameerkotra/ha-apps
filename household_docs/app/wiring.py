"""Where the extras of steps 8–12 plug into the core (SPEC §2's extension points): extra fields on items, the open
document, an admin shared folder's top and /api/me, and what runs after every 🕑 Activity record."""
from . import activity, app_messages, changes, filing, ha_sensors, notify, settings
from .routers import me as me_router, nodes as nodes_router
from . import documents


def _following(conn, user, ids) -> set:
    ids = [i for i in ids if i]
    if not ids:
        return set()
    q = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(f"SELECT target FROM follows WHERE user_id = ? AND target IN ({q})",
                                       (user["id"], *ids))}


def row_extras(conn, user, rows) -> dict:
    ids = [r["id"] for r in rows]
    fol = _following(conn, user, ids)
    sens = ha_sensors.for_ids(conn, ids)
    out = {}
    pdf_ids = [r["id"] for r in rows if (r["ext"] or "") == "pdf"]
    text = {}
    if pdf_ids or any(r["kind"] == "file" for r in rows):
        from . import pdftext
        text = pdftext.status_for(conn, [r["id"] for r in rows if r["kind"] == "file"])
    folder_ids = [r["id"] for r in rows if r["kind"] == "folder"]
    ai_on = set()
    if folder_ids:
        q = ",".join("?" * len(folder_ids))
        ai_on = {x[0] for x in conn.execute(f"SELECT target FROM ai_folders WHERE target IN ({q})", folder_ids)}
    rules = filing.folder_extras(conn, folder_ids)                 # §17.18–§17.19: rules on folders
    seen = changes.row_extras(conn, user, rows)                    # §17.21: changed since you looked
    for r in rows:
        e = {}
        e.update(rules.get(r["id"]) or {})
        e.update(seen.get(r["id"]) or {})
        if r["id"] in ai_on:
            e["aiScans"] = True
        if r["id"] in fol:
            e["following"] = True
        if r["id"] in sens:
            e["haSensor"] = sens[r["id"]]
        if r["id"] in text:
            e.update(text[r["id"]])
        if e:
            out[r["id"]] = e
    return out


def root_extras(conn, user, ref) -> dict:
    from . import ai
    out = {"following": bool(_following(conn, user, [ref])), "haSensor": ha_sensors.entity_for(conn, ref),
           "aiScans": ai.folder_opted_in(conn, ref)}
    out.update(filing.folder_extras(conn, [ref]).get(ref) or {})
    return out


def meta_extras(conn, user, node) -> dict:
    return {"following": bool(_following(conn, user, [node["id"]])), "haSensor": ha_sensors.entity_for(conn, node["id"])}


def me_extras(conn, user) -> dict:
    from . import ai
    from .routers.nodes import children_of
    v = settings.all_values(conn)
    child = bool(user.get("is_child"))
    kids_view = []
    for cid, name in children_of(conn, user).items():          # §17.20: children whose My docs this person may view
        r = conn.execute("SELECT id FROM roots WHERE kind = 'person' AND user_id = ?", (cid,)).fetchone()
        if r is not None:
            kids_view.append({"rootId": r["id"], "childId": cid, "name": name})
    return {"follows": [r["target"] for r in conn.execute("SELECT target FROM follows WHERE user_id = ?", (user["id"],))],
            "extras": {"haSensors": bool(v["ha_sensors"]), "keepImport": bool(v["keep_import"]),
                       "pdfIndex": int(v["pdf_index_mb"]) > 0, "activityDays": int(v["activity_days"])},
            "ai": {"on": False, "provider": None, "address": None, "model": None, "visionModel": None} if child
            else ai.public_state(conn),
            "isChild": child, "kidsStatus": notify.child_status(conn, user["id"]) if child else None, "kidsView": sorted(kids_view, key=lambda k: k["name"].casefold()),
            "apps": ({"chat": False, "todo": app_messages.status()["todo"], "panel": app_messages.panel()} if child
                     else app_messages.status())}


def install() -> None:
    if row_extras not in nodes_router.ROW_EXTRAS:
        nodes_router.ROW_EXTRAS.append(row_extras)
        nodes_router.ROOT_EXTRAS.append(root_extras)
        documents.META_EXTRAS.append(meta_extras)
        me_router.ME_EXTRAS.append(me_extras)
        activity.HOOKS.append(ha_sensors.on_activity)
        activity.HOOKS.append(filing.on_activity)
