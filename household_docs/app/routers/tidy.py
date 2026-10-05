"""Templates, filing and clean-up rules, and "what changed since you last looked" (SPEC §17.17–§17.19, §17.21)."""
from fastapi import APIRouter, Body, Depends
from pydantic import Field

from .. import changes, db, filing, templates
from ..auth import require_user
from .common import Strict
from .nodes import node_info_json

router = APIRouter(prefix="/api", tags=["tidy"])


# ---------------------------------------------------------------- templates (§17.17)
@router.get("/templates")
def list_templates(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return templates.listing(conn, user)


class UseIn(Strict):
    ref: str = Field(min_length=3, max_length=300)
    name: str = Field(min_length=1, max_length=250)
    parentId: str | None = Field(default=None, max_length=70)


@router.post("/templates/use", status_code=201)
def use_template(body: UseIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        nid = templates.use(conn, user, body.ref, body.name, body.parentId)
        return node_info_json(conn, user, nid)


class SaveTemplateIn(Strict):
    name: str = Field(min_length=1, max_length=200)
    household: bool = False


@router.post("/docs/{node_id}/save-template", status_code=201)
def save_template(node_id: str, body: SaveTemplateIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return templates.save(conn, user, node_id, body.name, body.household)


@router.delete("/templates/{ref}")
def remove_template(ref: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        templates.remove(conn, user, ref[:300])
        return templates.listing(conn, user)


# ---------------------------------------------------------------- filing and clean-up rules (§17.18–§17.19)
@router.get("/rules/{ref}")
def get_rules(ref: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.rules_json(conn, user, ref[:70])


@router.post("/rules/{ref}/filing", status_code=201)
def add_filing_rule(ref: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.add_rule(conn, user, ref[:70], body)


@router.put("/rules/{ref}/filing/{rule_id}")
def change_filing_rule(ref: str, rule_id: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.change_rule(conn, user, ref[:70], rule_id, body)


@router.delete("/rules/{ref}/filing/{rule_id}")
def remove_filing_rule(ref: str, rule_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.remove_rule(conn, user, ref[:70], rule_id)


@router.post("/rules/{ref}/filing/order")
def order_filing_rules(ref: str, body: dict = Body(...), user: dict = Depends(require_user)):
    """{ids: [rule ids in the new order]}."""
    with db.get_conn() as conn:
        return filing.reorder(conn, user, ref[:70], (body or {}).get("ids"))


@router.post("/rules/{ref}/filing/dry-run")
def dry_run(ref: str, body: dict = Body(...), user: dict = Depends(require_user)):
    """Show what would happen: the rule (as it would be saved) against the files already in the folder."""
    with db.get_conn() as conn:
        return filing.dry_run(conn, user, ref[:70], body)


@router.put("/rules/{ref}/cleanup")
def set_cleanup(ref: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.set_cleanup(conn, user, ref[:70], body)


@router.delete("/rules/{ref}/cleanup")
def remove_cleanup(ref: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return filing.remove_cleanup(conn, user, ref[:70])


@router.post("/filing/{log_id}/undo")
def undo_filing(log_id: str, user: dict = Depends(require_user)):
    """🕑 Activity → Undo (7 days): the file goes back where it arrived, under the name it had."""
    with db.get_conn() as conn:
        out = filing.undo(conn, user, log_id)
        return node_info_json(conn, user, out["id"])


# ---------------------------------------------------------------- what changed (§17.21)
@router.post("/nodes/{ref}/seen")
def mark_seen(ref: str, user: dict = Depends(require_user)):
    """Mark all as seen: a folder (`mine`, a folder id or `root:<id>`) and everything inside it you can open."""
    with db.get_conn() as conn:
        n = changes.mark_seen(conn, user, None if ref == "mine" else ref[:70])
    return {"ok": True, "items": n}

