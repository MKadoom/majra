"""GET /social-metrics implementation (Facebook Pages / Instagram).

Aggregates fact_social_performance. impressions/reach/engagement are daily flows
and get summed; `followers` is an as-of-date snapshot, so it is maxed over the
period (an approximation of the latest follower count) rather than summed.
"""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, FactSocialPerformance as S

GROUPABLE = {
    "date": S.date.label("date"),
    "grain": S.grain.label("grain"),
    "external_ref": S.external_ref.label("external_ref"),
    "account": Account.name.label("account"),
}

SUMS = {
    "impressions": func.coalesce(func.sum(S.impressions), 0),
    "reach": func.coalesce(func.sum(S.reach), 0),
    "engagement": func.coalesce(func.sum(S.engagement), 0),
    "followers": func.coalesce(func.max(S.followers), 0),   # snapshot -> max
}

SORTABLE = set(GROUPABLE) | set(SUMS)


def query_social_metrics(
    db: Session,
    date_from: date,
    date_to: date,
    account_ids: list[int] | None = None,
    grain: str | None = None,
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

    stmt = select(*cols, *aggs).select_from(S).join(Account, Account.id == S.account_id)
    stmt = stmt.where(S.date >= date_from, S.date <= date_to)
    if account_ids:
        stmt = stmt.where(S.account_id.in_(account_ids))
    if grain:
        stmt = stmt.where(S.grain == grain)
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
        for k in ("impressions", "reach", "engagement", "followers"):
            row[k] = int(row[k])
        data.append(row)

    return {"data": data, "page": page, "page_size": page_size, "total": int(total or 0)}
