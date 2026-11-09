"""GET /metrics implementation (SPEC §5, §5a).

Aggregates fact rows with whitelisted group-bys and filters. Derived metrics
(ctr, cpc, cpm, cpa, roas) are computed from the aggregated sums, never stored.
Always filters on a single breakdown set (default 'none') to prevent the
double-counting trap described in SPEC §3.

Money is always reported in ONE currency (default SAR, SPEC §5a): each row's
spend/value is converted inside the SUM. The only unconverted view is
group_by=currency. A currency with no FX rate is excluded from money sums and
reported in `warnings`, never passed through at 1.0.
"""
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Ad,
    Adset,
    Campaign,
    Connection,
    FxRate,
    MetricCoverage,
    TrackingChange,
)
from app.models import FactAdPerformance as F

DEFAULT_CURRENCY = "SAR"
# The riyal is pegged: 1 USD = 3.75 SAR. Use the peg, not a market quote.
PEGGED_RATE_TO_USD = {"SAR": 1 / 3.75}

GROUPABLE = {
    "platform": F.platform.label("platform"),
    "date": F.date.label("date"),
    "country": F.country.label("country"),
    "device": F.device.label("device"),
    "account": Account.name.label("account"),
    "campaign": Campaign.name.label("campaign"),
    "adset": Adset.name.label("adset"),
    "ad": Ad.name.label("ad"),
    "currency": F.currency.label("currency"),
    "cost_source": F.cost_source.label("cost_source"),
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
    "messages_sent": func.coalesce(func.sum(F.messages_sent), 0),
    "messages_delivered": func.coalesce(func.sum(F.messages_delivered), 0),
}
MONEY = ("spend", "conversion_value")
INTS = ("impressions", "clicks", "video_views", "leads", "purchases",
        "messages_sent", "messages_delivered")

SORTABLE = set(GROUPABLE) | set(SUMS)


def _derive(row: dict) -> dict:
    spend, imp = float(row["spend"]), int(row["impressions"])
    clicks, purchases = int(row["clicks"]), int(row["purchases"])
    value = float(row["conversion_value"])
    # SPEC §5a: purchase_value is the donation/purchase value; conversion_value
    # stays as an alias for one release (Looker Studio / MCP / saved reports).
    row["purchase_value"] = value
    row["ctr"] = round(clicks / imp * 100, 4) if imp else None
    row["cpc"] = round(spend / clicks, 4) if clicks else None
    row["cpm"] = round(spend / imp * 1000, 4) if imp else None
    row["cpa"] = round(spend / purchases, 4) if purchases else None
    row["roas"] = round(value / spend, 4) if spend else None
    return row


def _scoped(stmt, date_from, date_to, breakdown, platforms, account_ids,
            campaign_ids, user_id):
    """FROM facts + the filters every metrics query shares: this user's
    connections only (tenant isolation), soft-deleted connections excluded."""
    stmt = (
        stmt.select_from(F)
        .join(Account, Account.id == F.account_id)
        .join(Connection, Connection.id == Account.connection_id)
        .where(Connection.deleted_at.is_(None),
               F.date >= date_from, F.date <= date_to, F.breakdown == breakdown)
    )
    if user_id is not None:
        stmt = stmt.where(Connection.user_id == user_id)
    if platforms:
        stmt = stmt.where(F.platform.in_(platforms))
    if account_ids:
        stmt = stmt.where(F.account_id.in_(account_ids))
    if campaign_ids:
        stmt = stmt.where(F.campaign_id.in_(campaign_ids))
    return stmt


def _cost_source_warnings(db: Session, *scope) -> list[str]:
    """Spend that isn't a billed figure must never pass silently (T6)."""
    rows = db.execute(
        _scoped(select(F.platform, F.cost_source, func.count(func.distinct(F.date))),
                *scope)
        .where(F.cost_source.in_(("estimate", "missing")))
        .group_by(F.platform, F.cost_source)
        .order_by(F.platform, F.cost_source)
    ).all()
    out = []
    for platform, source, days in rows:
        if source == "estimate":
            out.append(f"{platform} spend is estimated (delivered × configured price) "
                       f"on {days} day(s) — Meta returned no cost for them")
        else:
            out.append(f"{platform} spend is unknown on {days} day(s) — counted as 0")
    return out


OVERLAP_METRICS = ("purchases", "purchase_value", "conversion_value", "cpa", "roas")
OVERLAP_NOTE = (
    "purchases/value/CPA/ROAS add each platform's own attributed count; "
    "one donation can appear once per platform"
)


def purchase_overlap(db: Session, *scope) -> dict | None:
    """Each ad platform attributes the same donation to itself, so a total that
    spans platforms is NOT a de-duplicated donation count. Returns the overlap
    descriptor when more than one platform in scope reports purchases, else
    None. `scope` = _scoped's filter args (the platform filter is honoured, so
    a single-platform filter never warns)."""
    found = sorted(
        p for p in db.scalars(
            _scoped(select(F.platform).distinct(), *scope).where(F.purchases > 0)
        ) if p
    )
    if len(found) < 2:
        return None
    return {"platforms": found, "metrics": list(OVERLAP_METRICS), "note": OVERLAP_NOTE}


def fx_rates(db: Session) -> dict[str, float]:
    """rate_to_usd per currency: stored rates, USD = 1, pegged currencies pinned."""
    rates = {r.currency.upper(): float(r.rate_to_usd) for r in db.scalars(select(FxRate))}
    rates["USD"] = 1.0
    rates.update(PEGGED_RATE_TO_USD)
    return rates


def _coverage_warnings(db: Session, date_from: date, date_to: date,
                       platforms: list[str] | None) -> list[str]:
    out = []
    for c in db.scalars(select(MetricCoverage).order_by(MetricCoverage.platform)):
        if platforms and c.platform not in platforms:
            continue
        if date_from < c.valid_from <= date_to or date_to < c.valid_from:
            out.append(
                f"{c.platform} {c.metric} only available from {c.valid_from.isoformat()}"
                " — earlier days in this range are incomplete"
            )
    return out


def _tracking_change_warnings(db: Session, date_from: date, date_to: date,
                              platforms: list[str] | None) -> list[str]:
    """A range that straddles a tracking change compares numbers that don't mean
    the same thing on both sides of it (SPEC §5a)."""
    out = []
    q = select(TrackingChange).where(TrackingChange.changed_on > date_from,
                                     TrackingChange.changed_on <= date_to)
    for c in db.scalars(q.order_by(TrackingChange.changed_on, TrackingChange.platform,
                                   TrackingChange.id)):
        if platforms and c.platform and c.platform not in platforms:
            continue
        who = c.platform or "all platforms"
        out.append(f"{who} {c.metric} tracking changed on {c.changed_on.isoformat()}: "
                   f"{c.note} — days before and after are not like for like")
    return out


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
    reporting_currency: str | None = DEFAULT_CURRENCY,
    user_id: int | None = None,
) -> dict:
    group_by = [g for g in (group_by or []) if g in GROUPABLE]
    # The 1000-row cap lives at the HTTP boundary (Query(le=1000)); exports
    # legitimately ask for far more, so the service itself doesn't clamp.
    page_size = max(1, page_size)

    # Currency (SPEC §5a): one reporting currency, default SAR. group_by=currency
    # is the only unconverted view (each row is a single currency already).
    sums = dict(SUMS)
    warnings: list[str] = []
    currency_out: str | None = None
    if "currency" not in group_by:
        currency_out = (reporting_currency or DEFAULT_CURRENCY).upper()
        rates = fx_rates(db)
        r_report = rates.get(currency_out)
        if not r_report:
            raise ValueError(f"no FX rate for reporting currency {currency_out}")
        factor = case(
            *[(F.currency == ccy, rate / r_report) for ccy, rate in rates.items()],
            else_=0.0,  # no rate → excluded (and warned about below), never 1.0
        )
        for m in MONEY:
            sums[m] = func.coalesce(func.sum(getattr(F, m) * factor), 0)
        unrated = sorted(
            c or "unknown" for c in db.scalars(
                _scoped(select(F.currency).distinct(), date_from, date_to,
                        breakdown, platforms, account_ids, campaign_ids, user_id)
            ) if (c or "").upper() not in rates
        )
        warnings += [f"no FX rate for {c} — its spend and value are excluded"
                     for c in unrated]
    warnings += _coverage_warnings(db, date_from, date_to, platforms)
    warnings += _tracking_change_warnings(db, date_from, date_to, platforms)
    warnings += _cost_source_warnings(db, date_from, date_to, breakdown, platforms,
                                      account_ids, campaign_ids, user_id)
    # Rows grouped by platform are each one platform's own claim; any other
    # shape (ungrouped, by date, …) sums claims across platforms.
    overlap = None
    if "platform" not in group_by:
        overlap = purchase_overlap(db, date_from, date_to, breakdown, platforms,
                                   account_ids, campaign_ids, user_id)
        if overlap:
            warnings.append(overlap["note"])

    cols = [GROUPABLE[g] for g in group_by]
    aggs = [expr.label(name) for name, expr in sums.items()]

    stmt = _scoped(select(*cols, *aggs), date_from, date_to, breakdown,
                   platforms, account_ids, campaign_ids, user_id)
    if "campaign" in group_by:
        stmt = stmt.join(Campaign, Campaign.id == F.campaign_id, isouter=True)
    if "adset" in group_by:
        stmt = stmt.join(Adset, Adset.id == F.adset_id, isouter=True)
    if "ad" in group_by:
        stmt = stmt.join(Ad, Ad.id == F.ad_id, isouter=True)

    if cols:
        stmt = stmt.group_by(*[GROUPABLE[g] for g in group_by])
    else:
        # An ungrouped aggregate always yields one row; with no matching facts
        # that's a fake all-zero total. Return no rows instead.
        stmt = stmt.having(func.count() > 0)

    if cols:
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    else:
        total = None  # 0 or 1, known once the row is fetched

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
        for k in INTS:
            row[k] = int(row[k])
        data.append(_derive(row))
    if total is None:
        total = len(data)

    return {
        "data": data,
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
        "currency": currency_out,
        "warnings": warnings,
        "purchase_overlap": overlap,
    }
