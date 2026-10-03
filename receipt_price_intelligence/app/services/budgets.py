"""Monthly budgets: limits per category or for everything, and how the month is going."""

from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Budget
from app.services import analytics, insights
from app.services.analysis_data import load_observations, load_receipts
from app.services.categories import clean_category

MAX_AMOUNT = 10_000_000


class BudgetError(ValueError):
    """A budget that cannot be saved. The message is safe to show."""


def _month_start(d: date) -> date:
    return d.replace(day=1)


def month_spend(db: Session, home_id: str, today: date) -> tuple[float, dict[str, float]]:
    """(everything spent this month, spend per category) so far."""
    start = _month_start(today).isoformat()
    total = analytics.summarize_spend(load_receipts(db, home_id, start))["totals"]["spend"] or 0.0
    per_category: dict[str, float] = {}
    for r in load_observations(db, home_id, start):
        key = r.get("category") or insights.UNCATEGORIZED
        per_category[key] = per_category.get(key, 0.0) + (r.get("line_total") or 0.0)
    return total, per_category


def list_budgets(db: Session, home_id: str, today: date | None = None) -> list[dict[str, Any]]:
    """Every budget with this month's status, the overall one first."""
    today = today or date.today()
    budgets = db.query(Budget).filter(Budget.home_id == home_id).all()
    if not budgets:
        return []
    total, per_category = month_spend(db, home_id, today)
    per_category = {k.lower(): v for k, v in per_category.items()}  # categories match regardless of capitals
    out = []
    for b in budgets:
        spent = total if b.category is None else per_category.get(b.category.lower(), 0.0)
        out.append({"id": b.id, "category": b.category, **insights.budget_status(b.monthly_amount, spent, today)})
    out.sort(key=lambda x: (x["category"] is not None, (x["category"] or "").lower()))
    return out


def save_budget(db: Session, home_id: str, category: str | None, amount: Any) -> Budget:
    """Create a budget, or change the amount if that category already has one."""
    try:
        value = float(amount)
    except (TypeError, ValueError):
        raise BudgetError("Enter the monthly amount as a number")
    if not 0 < value <= MAX_AMOUNT:
        raise BudgetError("The monthly amount must be more than zero")
    cat = clean_category(category)

    query = db.query(Budget).filter(Budget.home_id == home_id)
    query = query.filter(Budget.category.is_(None)) if cat is None else query.filter(func.lower(Budget.category) == cat.lower())
    budget = query.first()
    if budget is None:
        budget = Budget(home_id=home_id, category=cat)
        db.add(budget)
    budget.monthly_amount = round(value, 2)
    db.commit()
    db.refresh(budget)
    return budget


def delete_budget(db: Session, budget_id: str) -> bool:
    budget = db.get(Budget, budget_id)
    if budget is None:
        return False
    db.delete(budget)
    db.commit()
    return True
