"""Small helpers the API modules share."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services import homes as homes_service


def require_home(db: Session, home_id: str) -> None:
    """404 unless the home exists."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
