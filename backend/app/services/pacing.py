"""Budget pacing.

For each enabled budget, compare month-to-date spend (blended into the budget's
currency via the fx_rates table) against the monthly amount and the fraction of
the month elapsed. A linear projection to month-end gives a status:

  over      — projected month-end spend exceeds the budget
  under     — projected spend is well below budget (< 75%)
  on_track  — in between

`pace` is the projected month-end spend; `pct_used` is spent / budget so far.
"""
import calendar
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget
from app.services.metrics import query_metrics

UNDER_FLOOR = 0.75  # projected < 75% of budget reads as "under"


def _mtd_spend(db: Session, budget: Budget, first: date, today: date) -> float:
    res = query_metrics(
        db,
        date_from=first,
        date_to=today,
        breakdown="none",
        platforms=[budget.platform] if budget.platform else None,
        account_ids=[budget.account_id] if budget.account_id else None,
        reporting_currency=budget.currency or "USD",
        user_id=budget.user_id,
    )
    return float(res["data"][0]["spend"]) if res["data"] else 0.0


def pacing_report(db: Session, today: date | None = None) -> list[dict]:
    today = today or date.today()
    first = today.replace(day=1)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed = today.day / days_in_month  # fraction of the month elapsed (incl today)

    out: list[dict] = []
    for b in db.scalars(
        select(Budget).where(Budget.enabled.is_(True)).order_by(Budget.id)
    ):
        budget = float(b.amount_monthly)
        spent = _mtd_spend(db, b, first, today)
        projected = spent / elapsed if elapsed else 0.0
        if budget <= 0:
            status = "on_track"
        elif projected > budget:
            status = "over"
        elif projected < budget * UNDER_FLOOR:
            status = "under"
        else:
            status = "on_track"
        out.append(
            {
                "budget_id": b.id,
                "name": b.name,
                "platform": b.platform,
                "account_id": b.account_id,
                "currency": b.currency or "USD",
                "amount_monthly": budget,
                "spent_mtd": round(spent, 2),
                "projected_month_end": round(projected, 2),
                "pct_used": round(spent / budget, 4) if budget else None,
                "status": status,
            }
        )
    return out


def over_budget(db: Session, today: date | None = None) -> list[dict]:
    return [r for r in pacing_report(db, today) if r["status"] == "over"]
