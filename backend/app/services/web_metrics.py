"""GET /web-metrics implementation (GA4 web analytics).

Mirrors the ad metrics service but over fact_web_performance: aggregates
sessions/users/conversions/revenue with a whitelisted set of group-bys
(source, medium, campaign, date). Adds no ad-specific derived KPIs.
"""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, FactWebPerformance as W

GROUPABLE = {
    "date": W.date.label("date"),
    "source": W.source.label("source"),
    "medium": W.medium.label("medium"),
    "campaign": W.campaign_name.label("campaign"),
    "account": Account.name.label("account"),
}

SUMS = {
    "sessions": func.coalesce(func.sum(W.sessions), 0),
    "users": func.coalesce(func.sum(W.users), 0),
    "conversions": func.coalesce(func.sum(W.conversions), 0),
    "revenue": func.coalesce(func.sum(W.revenue), 0),
}

SORTABLE = set(GROUPABLE) | set(SUMS)


def query_web_metrics(
    db: Session,
    date_from: date,
    date_to: date,
    account_ids: list[int] | None = None,
    sources: list[str] | None = None,
    group_by: list[str] | None = None,
    sort: str | None = None,
    page: int = 1,
    page_size: int = 100,
) -> dict:
    group_by = [g for g in (group_by or []) if g in GROUPABLE]
    # The 1000-row cap lives at the HTTP boundary (Query(le=1000)); exports
    # legitimately ask for far more, so the service itself doesn't clamp.
    page_size = max(1, page_size)

    cols = [GROUPABLE[g] for g in group_by]
    aggs = [expr.label(name) for name, expr in SUMS.items()]

    stmt = select(*cols, *aggs).select_from(W).join(Account, Account.id == W.account_id)
    stmt = stmt.where(W.date >= date_from, W.date <= date_to)
    if account_ids:
        stmt = stmt.where(W.account_id.in_(account_ids))
    if sources:
        stmt = stmt.where(W.source.in_(sources))
    if cols:
        stmt = stmt.group_by(*cols)
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    else:
        total = 1

    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-")
        if key in SORTABLE:
            target = GROUPABLE[key] if key in GROUPABLE else SUMS[key]
            stmt = stmt.order_by(target.desc() if desc else target.asc())
    elif "date" in group_by:
        stmt = stmt.order_by(GROUPABLE["date"].asc())

    stmt = stmt.limit(page_size).offset((page - 1) * page_size)

    data = []
    for r in db.execute(stmt).mappings():
        row = dict(r)
        if "date" in row and row["date"] is not None:
            row["date"] = row["date"].isoformat()
        row["sessions"] = int(row["sessions"])
        row["users"] = int(row["users"])
        row["conversions"] = float(row["conversions"])
        row["revenue"] = float(row["revenue"])
        data.append(row)

    return {"data": data, "page": page, "page_size": page_size, "total": int(total or 0)}
