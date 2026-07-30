"""GET /metrics implementation (SPEC §5).

Aggregates fact rows with whitelisted group-bys and filters. Derived metrics
(ctr, cpc, cpm, cpa, roas) are computed from the aggregated sums, never stored.
Always filters on a single breakdown set (default 'none') to prevent the
double-counting trap described in SPEC §3.
"""
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Account, Ad, Adset, Campaign, Connection, FxRate
from app.models import FactAdPerformance as F

GROUPABLE = {
    "platform": F.platform.label("platform"),
    "date": F.date.label("date"),
    "country": F.country.label("country"),
    "device": F.device.label("device"),
    "account": Account.name.label("account"),
    "campaign": Campaign.name.label("campaign"),
    "adset": Adset.name.label("adset"),
    "ad": Ad.name.label("ad"),
}

SUMS = {
    "spend": func.coalesce(func.sum(F.spend), 0),
    "impressions": func.coalesce(func.sum(F.impressions), 0),
    "clicks": func.coalesce(func.sum(F.clicks), 0),
    "conversions": func.coalesce(func.sum(F.conversions), 0),
    "conversion_value": func.coalesce(func.sum(F.conversion_value), 0),
    "video_views": func.coalesce(func.sum(F.video_views), 0),
    "leads": func.coalesce(func.sum(F.leads), 0),
    "purchases": func.coalesce(func.sum(F.purchases), 0),
}

SORTABLE = set(GROUPABLE) | set(SUMS)


def _derive(row: dict) -> dict:
    spend, imp = float(row["spend"]), int(row["impressions"])
    clicks, conv = int(row["clicks"]), float(row["conversions"])
    value = float(row["conversion_value"])
    row["ctr"] = round(clicks / imp * 100, 4) if imp else None
    row["cpc"] = round(spend / clicks, 4) if clicks else None
    row["cpm"] = round(spend / imp * 1000, 4) if imp else None
    row["cpa"] = round(spend / conv, 4) if conv else None
    row["roas"] = round(value / spend, 4) if spend else None
    return row


def query_metrics(
    db: Session,
    date_from: date,
    date_to: date,
    breakdown: str = "none",
    platforms: list[str] | None = None,
    account_ids: list[int] | None = None,
    campaign_ids: list[int] | None = None,
    group_by: list[str] | None = None,
    sort: str | None = None,
    page: int = 1,
    page_size: int = 100,
    reporting_currency: str | None = None,
    user_id: int | None = None,
) -> dict:
    group_by = [g for g in (group_by or []) if g in GROUPABLE]
    # The 1000-row cap lives at the HTTP boundary (Query(le=1000)); exports
    # legitimately ask for far more, so the service itself doesn't clamp.
    page_size = max(1, page_size)

    # Currency normalization (SPEC §3): spend/value are stored in each account's
    # own currency. When a reporting currency is requested, multiply each row's
    # monetary values by rate_to_usd[row]/rate_to_usd[reporting] inside the SUM,
    # so a blended SAR+USD total is meaningful. Unknown currencies fall back to
    # factor 1.0 (they pass through unconverted rather than vanish).
    sums = dict(SUMS)
    currency_out: str | None = None
    if reporting_currency:
        rates = {r.currency: float(r.rate_to_usd) for r in db.scalars(select(FxRate))}
        rates.setdefault("USD", 1.0)
        r_report = rates.get(reporting_currency)
        if r_report:
            factor = case(
                *[(F.currency == ccy, rate / r_report) for ccy, rate in rates.items()],
                else_=1.0,
            )
            sums["spend"] = func.coalesce(func.sum(F.spend * factor), 0)
            sums["conversion_value"] = func.coalesce(
                func.sum(F.conversion_value * factor), 0
            )
            currency_out = reporting_currency

    cols = [GROUPABLE[g] for g in group_by]
    aggs = [expr.label(name) for name, expr in sums.items()]

    stmt = select(*cols, *aggs).select_from(F)
    stmt = stmt.join(Account, Account.id == F.account_id)
    # Scope to this user's connections (tenant isolation) and exclude facts from
    # soft-deleted connections (recycle bin).
    stmt = stmt.join(Connection, Connection.id == Account.connection_id).where(
        Connection.deleted_at.is_(None)
    )
    if user_id is not None:
        stmt = stmt.where(Connection.user_id == user_id)
    if "campaign" in group_by:
        stmt = stmt.join(Campaign, Campaign.id == F.campaign_id, isouter=True)
    if "adset" in group_by:
        stmt = stmt.join(Adset, Adset.id == F.adset_id, isouter=True)
    if "ad" in group_by:
        stmt = stmt.join(Ad, Ad.id == F.ad_id, isouter=True)

    stmt = stmt.where(
        F.date >= date_from, F.date <= date_to, F.breakdown == breakdown
    )
    if platforms:
        stmt = stmt.where(F.platform.in_(platforms))
    if account_ids:
        stmt = stmt.where(F.account_id.in_(account_ids))
    if campaign_ids:
        stmt = stmt.where(F.campaign_id.in_(campaign_ids))
    if cols:
        stmt = stmt.group_by(*[GROUPABLE[g] for g in group_by])

    # total number of groups (1 when no group_by)
    if cols:
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    else:
        total = 1

    # sorting
    desc = False
    if sort:
        desc = sort.startswith("-")
        key = sort.lstrip("-")
        if key in SORTABLE:
            # NB: can't use `GROUPABLE.get(key) or SUMS[key]` — a SQLAlchemy
            # clause element has no defined truthiness.
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
        for k in ("spend", "conversions", "conversion_value"):
            row[k] = float(row[k])
        for k in ("impressions", "clicks", "video_views", "leads", "purchases"):
            row[k] = int(row[k])
        data.append(_derive(row))

    return {
        "data": data,
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
        "currency": currency_out,
    }
