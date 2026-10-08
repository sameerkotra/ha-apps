"""Kids' space (SPEC §17.20): what a child's account can't do, enforced on the server.

An admin marks a person as a child (Admin → People). A child:
- sees their own My docs, what is shared with them by name, and admin shared folders marked **Kids folder** that
  they have access to — never Everyone shares and never other shared folders (sharing.py: the access CTE, role_of,
  root_role, share_folders.for_user);
- writes notes and checklists — no sheets (opening, saving, making, importing, exporting or converting one is
  refused, documents.py / sheets.py / docops.py / templates.py — Make a copy too: no sheet, no folder holding one);
- can't share (Share…, Transfer, Send to chat with or without members' access), can't use AI, can't search with
  regular expressions (search/engine.py).

Two layers: the routes in BLOCKED answer 403 to a child before anything else runs (auth.require_user); the
routes in CHECKED are allowed but refuse the parts above where the content is known. Every other /api route a
person can use is in ALLOWED — tests/test_tidy.py walks the app's route table and fails when a route is in none
of the three, so a new route needs a decision here.

Parents: an admin may let chosen people view a child's My docs (`kid_parents`); they get Can view on everything
in it (sharing.py) and see it under 🧒 Kids' docs.
"""
MESSAGE = "This isn't available in Kids' space."

# (method, route path) a child can't use at all
BLOCKED = {
    ("GET", "/api/nodes/{node_id}/shares"), ("POST", "/api/nodes/{node_id}/shares"),
    ("DELETE", "/api/nodes/{node_id}/shares/{target}"),
    ("POST", "/api/nodes/{node_id}/link"), ("DELETE", "/api/nodes/{node_id}/link"),     # view links (§6.6)
    ("GET", "/api/view-links/{token}"),
    ("POST", "/api/nodes/{node_id}/transfer"),
    ("GET", "/api/docs/{node_id}/export"), ("POST", "/api/docs/{node_id}/edit-copy"), ("POST", "/api/import"),
    ("POST", "/api/nodes/{node_id}/chat/chats"), ("POST", "/api/nodes/{node_id}/chat/card"),
}
# every route under this prefix (the AI, §17.6)
BLOCKED_PREFIXES = ("/api/ai",)

# allowed, but the content decides (sheets, regular expressions, Everyone, shared folders)
CHECKED = {
    ("POST", "/api/docs"), ("GET", "/api/docs/{node_id}"), ("PATCH", "/api/docs/{node_id}"),
    ("POST", "/api/docs/{node_id}/convert"), ("GET", "/api/search"), ("GET", "/api/search/export"),
    ("GET", "/api/space/{space}"), ("GET", "/api/shared-folders"), ("GET", "/api/list"), ("GET", "/api/folders"),
    ("POST", "/api/templates/use"), ("POST", "/api/docs/{node_id}/save-template"),
    ("POST", "/api/docs/{node_id}/versions/{n}/restore"),
    ("POST", "/api/nodes/{node_id}/copy"),          # Make a copy: not of a sheet, nor a folder holding one
}

# allowed as for anyone (their own role on each item still decides)
ALLOWED = {
    ("GET", "/api/me"), ("PUT", "/api/me/settings"), ("GET", "/api/whoami"), ("GET", "/api/people"),
    ("GET", "/api/nodes/{node_id}"), ("POST", "/api/nodes/{node_id}/rename"), ("POST", "/api/nodes/{node_id}/move"),
    ("POST", "/api/nodes/{node_id}/leave"), ("POST", "/api/nodes/{node_id}/hide"),
    ("POST", "/api/nodes/{node_id}/favourite"), ("DELETE", "/api/nodes/{node_id}"),
    ("POST", "/api/trash/{trash_id}/restore"), ("POST", "/api/trash/empty"), ("GET", "/api/nodes/{node_id}/file"),
    ("GET", "/api/docs/{node_id}/etag"), ("POST", "/api/docs/{node_id}/checklist"),
    ("GET", "/api/docs/{node_id}/versions"), ("GET", "/api/docs/{node_id}/versions/{n}"),
    ("GET", "/api/docs/{node_id}/versions/{n}/file"),
    ("POST", "/api/nodes/{ref}/upload"), ("GET", "/api/nodes/{node_id}/preview"), ("GET", "/api/nodes/{ref}/zip"),
    ("GET", "/api/zip"),
    ("GET", "/api/searches"), ("POST", "/api/searches"), ("PATCH", "/api/searches/{sid}"),
    ("DELETE", "/api/searches/recent"), ("DELETE", "/api/searches/{sid}"), ("GET", "/api/searches/recent"),
    ("POST", "/api/searches/recent"),
    ("GET", "/api/tags"), ("GET", "/api/nodes/{node_id}/tags"), ("PUT", "/api/nodes/{node_id}/tags"),
    ("POST", "/api/nodes/{node_id}/tags"), ("GET", "/api/docs/{node_id}/links"), ("GET", "/api/docs/{node_id}/pdf"),
    ("POST", "/api/docs/{node_id}/pdf"), ("GET", "/api/me/pins"), ("PUT", "/api/me/pins"),
    ("POST", "/api/nodes/{node_id}/pin"), ("POST", "/api/quick-note"), ("POST", "/api/quick-note/{node_id}/finish"),
    ("GET", "/api/activity"), ("GET", "/api/follows"), ("POST", "/api/follows"), ("DELETE", "/api/follows/{target}"),
    ("GET", "/api/nodes/{ref}/ha-sensor"), ("PUT", "/api/nodes/{ref}/ha-sensor"), ("DELETE", "/api/nodes/{ref}/ha-sensor"),
    ("GET", "/api/reports/storage"), ("POST", "/api/reports/duplicates/keep"),
    ("POST", "/api/nodes/{ref}/scan"), ("POST", "/api/import/{kind}/upload"), ("POST", "/api/import/{kind}/{token}"),
    ("GET", "/api/import/jobs/{job_id}"), ("GET", "/api/nodes/{node_id}/text"), ("PUT", "/api/nodes/{node_id}/text"),
    # step 12
    ("GET", "/api/bus/requests/{request_id}"), ("POST", "/api/docs/{node_id}/todo/lists"),
    ("POST", "/api/docs/{node_id}/todo"), ("GET", "/api/templates"), ("DELETE", "/api/templates/{ref}"),
    ("GET", "/api/rules/{ref}"), ("POST", "/api/rules/{ref}/filing"), ("PUT", "/api/rules/{ref}/filing/{rule_id}"),
    ("DELETE", "/api/rules/{ref}/filing/{rule_id}"), ("POST", "/api/rules/{ref}/filing/order"),
    ("POST", "/api/rules/{ref}/filing/dry-run"), ("PUT", "/api/rules/{ref}/cleanup"),
    ("DELETE", "/api/rules/{ref}/cleanup"), ("POST", "/api/filing/{log_id}/undo"),
    ("POST", "/api/nodes/{ref}/seen"),
}


def blocked(method: str, path: str) -> bool:
    """True when a child may not use this route at all."""
    return (method, path) in BLOCKED or any(path == p or path.startswith(p + "/") for p in BLOCKED_PREFIXES)


def is_child(user: dict | None) -> bool:
    return bool(user and user.get("is_child"))


def refuse_sheet(user: dict, kind: str | None) -> None:
    """403 for a child and a sheet (open, save, make, convert, restore, template)."""
    if kind == "sheet" and is_child(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Sheets aren't available in Kids' space.")
