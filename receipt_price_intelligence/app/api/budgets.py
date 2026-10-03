"""Monthly budgets, per category or for everything."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.db import get_db
from app.services import budgets as budget_service
from app.services import homes as homes_service

router = APIRouter(prefix="/api/v1", tags=["budgets"])


class BudgetIn(BaseModel):
    category: str | None = Field(None, max_length=100, description="Empty for a budget on all spending")
    monthly_amount: float | str


@router.get("/homes/{home_id}/budgets")
async def list_budgets(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Budgets with this month's status: spent, remaining, percent used, projected month end, and
    ``status`` (``ok``, ``warn`` from 80% used or when the pace would overshoot, ``over``)."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    return budget_service.list_budgets(db, home_id)


@router.put("/homes/{home_id}/budgets")
async def save_budget(home_id: str, request: BudgetIn, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a budget, or change the amount if that category (or the overall budget) already has one."""
    if homes_service.get_home(db, home_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Home not found")
    try:
        budget_service.save_budget(db, home_id, request.category, request.monthly_amount)
    except budget_service.BudgetError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return budget_service.list_budgets(db, home_id)


@router.delete("/budgets/{budget_id}")
async def delete_budget(budget_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    if not budget_service.delete_budget(db, budget_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return {"status": "deleted", "id": budget_id}
