"""Small helpers the API modules share."""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services import homes as homes_service


def require_home(db: Session, home_id: str):
    """The home, or 404 (every API module checks a home this one way)."""
    home = homes_service.get_home(db, home_id)
    if home is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return home
