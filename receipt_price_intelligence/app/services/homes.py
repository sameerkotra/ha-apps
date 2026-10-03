"""Homes: shared households that receipts and price history belong to."""

from __future__ import annotations

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import CommonItem, Home, PriceObservation, Receipt, ReceiptDraft, RecommendationSnapshot
from app.logging_config import get_logger

logger = get_logger("homes")

MAX_NAME_LENGTH = 255


class HomeError(ValueError):
    """A home operation that cannot be done; the message is safe to show to the user."""


def _clean_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HomeError("A home needs a name")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise HomeError(f"Home names are limited to {MAX_NAME_LENGTH} characters")
    return cleaned


def list_homes(db: Session) -> list[Home]:
    return db.query(Home).order_by(func.lower(Home.name)).all()


def get_home(db: Session, home_id: str) -> Home | None:
    return db.get(Home, home_id)


def _name_taken(db: Session, name: str, exclude_id: str | None = None) -> bool:
    query = db.query(Home.id).filter(func.lower(Home.name) == name.lower())
    if exclude_id:
        query = query.filter(Home.id != exclude_id)
    return query.first() is not None


def create_home(db: Session, name: str, user_id: str) -> Home:
    name = _clean_name(name)
    if _name_taken(db, name):
        raise HomeError(f'A home named "{name}" already exists')
    home = Home(id=str(uuid.uuid4()), name=name, created_by_user_id=user_id)
    db.add(home)
    db.commit()
    db.refresh(home)
    logger.info("Created home %s (%s)", home.id, name)
    return home


def rename_home(db: Session, home_id: str, name: str) -> Home | None:
    home = get_home(db, home_id)
    if home is None:
        return None
    name = _clean_name(name)
    if _name_taken(db, name, exclude_id=home_id):
        raise HomeError(f'A home named "{name}" already exists')
    home.name = name
    db.commit()
    db.refresh(home)
    return home


def home_usage(db: Session, home_id: str) -> dict[str, int]:
    """Counts of data attached to a home."""
    return {
        "drafts": db.query(func.count(ReceiptDraft.id)).filter(ReceiptDraft.home_id == home_id).scalar() or 0,
        "receipts": db.query(func.count(Receipt.id)).filter(Receipt.home_id == home_id).scalar() or 0,
    }


def delete_home(db: Session, home_id: str) -> bool:
    """Delete an empty home. Homes that hold receipts or drafts cannot be deleted."""
    home = get_home(db, home_id)
    if home is None:
        return False
    usage = home_usage(db, home_id)
    if usage["drafts"] or usage["receipts"]:
        raise HomeError(
            "This home still has receipts. Deleting a home with data is not supported, "
            "so rename it instead."
        )
    # Items learned for this home hold no receipts, so they can go with it.
    db.query(CommonItem).filter(CommonItem.home_id == home_id).delete()
    db.query(RecommendationSnapshot).filter(RecommendationSnapshot.home_id == home_id).delete()
    db.delete(home)
    db.commit()
    logger.info("Deleted home %s", home_id)
    return True


def resolve_home_id(db: Session, home_id: str | None) -> str:
    """Validate the home a new receipt is added to.

    With no home given, the only existing home is used. Raises HomeError otherwise.
    """
    if home_id:
        if get_home(db, home_id) is None:
            raise HomeError("That home does not exist")
        return home_id

    homes = list_homes(db)
    if not homes:
        raise HomeError("No home exists yet. Ask an administrator to create one.")
    if len(homes) > 1:
        raise HomeError("Choose which home this receipt belongs to")
    return homes[0].id


def adopt_legacy_data(db: Session, user_id: str | None = None) -> int:
    """Attach data created before homes existed to a home. Returns rows updated.

    If any receipt, draft, item, or price row has no home, they are assigned to the
    oldest home, creating one called "Home" when none exists.
    """
    tables = (ReceiptDraft, Receipt, CommonItem, PriceObservation)
    orphaned = sum(
        db.query(func.count(t.id)).filter(t.home_id.is_(None)).scalar() or 0 for t in tables
    )
    if not orphaned:
        return 0

    home = db.query(Home).order_by(Home.created_at, Home.id).first()
    if home is None:
        home = Home(id=str(uuid.uuid4()), name="Home", created_by_user_id=user_id)
        db.add(home)
        db.flush()

    updated = 0
    for table in tables:
        updated += db.query(table).filter(table.home_id.is_(None)).update(
            {"home_id": home.id}, synchronize_session=False
        )
    db.commit()
    logger.info("Attached %d existing rows to home %s", updated, home.name)
    return updated
