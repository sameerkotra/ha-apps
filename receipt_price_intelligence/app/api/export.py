"""CSV export of receipts and lines."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import exporter
from app.services import homes as homes_service

router = APIRouter(prefix="/api/v1/homes/{home_id}/export", tags=["export"])


def _csv(text: str, name: str) -> Response:
    return Response(content=text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _prepare(db: Session, home_id: str, start: str | None, end: str | None) -> tuple[str | None, str | None]:
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    try:
        return exporter.parse_range(start, end)
    except exporter.ExportError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.get("/receipts.csv")
async def export_receipts(
    home_id: str, start: str | None = None, end: str | None = None, tag: str | None = None,
    _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    """One row per saved receipt. ``start`` and ``end`` (inclusive, YYYY-MM-DD) and ``tag`` are optional filters."""
    s, e = _prepare(db, home_id, start, end)
    text = exporter.to_csv(exporter.receipt_rows(db, home_id, s, e, tag), exporter.RECEIPT_COLUMNS)
    return _csv(text, f"receipts-{date.today().isoformat()}.csv")


@router.get("/items.csv")
async def export_items(
    home_id: str, start: str | None = None, end: str | None = None, tag: str | None = None,
    _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
):
    """One row per receipt line, with the item's common name and category."""
    s, e = _prepare(db, home_id, start, end)
    text = exporter.to_csv(exporter.item_rows(db, home_id, s, e, tag), exporter.ITEM_COLUMNS)
    return _csv(text, f"receipt-lines-{date.today().isoformat()}.csv")
