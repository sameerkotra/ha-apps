"""Common items: the names analysis groups purchases by.

A common item is what a person calls a product ("Milk, 2%, 1 gal"). Printed receipt
descriptions ("GV 2% MILK 1GAL") are *aliases* that point at it. Renaming or merging items
here changes how existing purchases are grouped, and never touches the receipts themselves.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import (
    CommonItem,
    CommonItemAlias,
    ItemPurchaseStats,
    WebDeal,
    PriceObservation,
    ReceiptDraftItem,
    ReceiptItem,
)
from app.logging_config import get_logger
from app.services.categories import clean_category, suggest_category
from app.services.receipt_records import clean_common_name, find_item_by_name

logger = get_logger("items")


class ItemError(ValueError):
    def __init__(self, message: str, conflict_id: str | None = None):
        super().__init__(message)
        self.conflict_id = conflict_id


def list_items(db: Session, home_id: str, q: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """Common items in a home with usage counts, most used first."""
    query = db.query(CommonItem).filter(CommonItem.home_id == home_id, CommonItem.status == "ACTIVE")
    if q and q.strip():
        query = query.filter(func.lower(CommonItem.name).contains(q.strip().lower()))
    items = query.all()

    aliases = dict(
        db.query(CommonItemAlias.common_item_id, func.count(CommonItemAlias.id)).group_by(CommonItemAlias.common_item_id).all()
    )
    usage = {
        item_id: (count, last)
        for item_id, count, last in db.query(
            PriceObservation.common_item_id, func.count(PriceObservation.id), func.max(PriceObservation.purchase_date)
        ).filter(PriceObservation.home_id == home_id).group_by(PriceObservation.common_item_id).all()
    }

    out = [{
        "id": i.id,
        "name": i.name,
        "name_confirmed": bool(i.name_confirmed),
        "category": i.category,
        "alias_count": aliases.get(i.id, 0),
        "purchase_count": usage.get(i.id, (0, None))[0],
        "last_purchase_date": usage.get(i.id, (0, None))[1],
    } for i in items]
    out.sort(key=lambda x: (-x["purchase_count"], x["name"].lower()))
    return out[: max(1, min(limit, 2000))]


def get_item(db: Session, item_id: str) -> dict[str, Any] | None:
    item = db.get(CommonItem, item_id)
    if item is None:
        return None
    aliases = db.query(CommonItemAlias).filter(CommonItemAlias.common_item_id == item_id).order_by(CommonItemAlias.alias).all()
    return {
        "id": item.id,
        "home_id": item.home_id,
        "name": item.name,
        "name_confirmed": bool(item.name_confirmed),
        "category": item.category,
        "status": item.status,
        "aliases": [{"alias": a.alias, "source": a.source} for a in aliases],
    }


def rename_item(db: Session, item_id: str, name: str) -> CommonItem | None:
    """Give an item its common name. Raises ItemError if another item already has that name."""
    item = db.get(CommonItem, item_id)
    if item is None or item.status != "ACTIVE":
        return None
    cleaned = clean_common_name(name)
    if cleaned is None:
        raise ItemError("A common name can't be empty")
    other = find_item_by_name(db, item.home_id, cleaned)
    if other is not None and other.id != item.id:
        raise ItemError(f'"{other.name}" already exists. Merge the two items instead.', conflict_id=other.id)
    item.name = cleaned
    item.name_confirmed = 1
    db.commit()
    db.refresh(item)
    return item


def merge_items(db: Session, source_id: str, target_id: str) -> dict[str, int]:
    """Fold ``source`` into ``target``: every printed name, purchase and price moves over."""
    if source_id == target_id:
        raise ItemError("Choose a different item to merge into")
    source, target = db.get(CommonItem, source_id), db.get(CommonItem, target_id)
    if source is None or target is None or source.status != "ACTIVE" or target.status != "ACTIVE":
        raise ItemError("Item not found")
    if source.home_id != target.home_id:
        raise ItemError("Items can only be merged within one home")

    try:
        keep = {
            a.normalized_alias
            for a in db.query(CommonItemAlias).filter(CommonItemAlias.common_item_id == target.id).all()
        }
        moved_aliases = 0
        for alias in db.query(CommonItemAlias).filter(CommonItemAlias.common_item_id == source.id).all():
            if alias.normalized_alias in keep:
                db.delete(alias)
            else:
                alias.common_item_id = target.id
                moved_aliases += 1

        receipt_lines = db.query(ReceiptItem).filter(ReceiptItem.common_item_id == source.id).update(
            {"common_item_id": target.id}, synchronize_session=False
        )
        prices = db.query(PriceObservation).filter(PriceObservation.common_item_id == source.id).update(
            {"common_item_id": target.id}, synchronize_session=False
        )
        db.query(ReceiptDraftItem).filter(ReceiptDraftItem.suggested_common_item_id == source.id).update(
            {"suggested_common_item_id": target.id, "suggested_common_item_name": target.name}, synchronize_session=False
        )
        db.query(WebDeal).filter(WebDeal.common_item_id == source.id).update({"common_item_id": target.id}, synchronize_session=False)
        db.query(ItemPurchaseStats).filter(ItemPurchaseStats.common_item_id == source.id).delete(synchronize_session=False)

        source.status = "MERGED"
        source.merged_into_id = target.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    logger.info("Merged item %s into %s", source_id, target_id)
    return {"aliases_moved": moved_aliases, "receipt_lines": receipt_lines, "price_observations": prices}


def set_category(db: Session, item_id: str, category: str | None) -> CommonItem | None:
    """Set (or clear, with an empty value) an item's category."""
    item = db.get(CommonItem, item_id)
    if item is None or item.status != "ACTIVE":
        return None
    item.category = clean_category(category)
    db.commit()
    db.refresh(item)
    return item


def categorize_uncategorized(db: Session, home_id: str) -> dict[str, int]:
    """Give every item that has no category the one its name suggests, where a suggestion exists."""
    items = db.query(CommonItem).filter(
        CommonItem.home_id == home_id, CommonItem.status == "ACTIVE", CommonItem.category.is_(None)).all()
    done = 0
    for item in items:
        suggestion = suggest_category(item.name)
        if suggestion:
            item.category = suggestion
            done += 1
    db.commit()
    return {"categorized": done, "left": len(items) - done}


def list_categories(db: Session, home_id: str) -> list[str]:
    """The suggested categories plus any others already in use in this home, in a sensible order."""
    from app.services.categories import CATEGORIES
    used = {c for (c,) in db.query(CommonItem.category).filter(
        CommonItem.home_id == home_id, CommonItem.status == "ACTIVE", CommonItem.category.isnot(None)).distinct().all()}
    extra = sorted(c for c in used if c.lower() not in {x.lower() for x in CATEGORIES})
    return CATEGORIES[:-1] + extra + CATEGORIES[-1:]
