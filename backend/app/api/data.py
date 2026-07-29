from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.keys import get_read_principal
from app.connectors.base import BREAKDOWN_SETS
from app.core.config import settings
from app.core.db import get_db
from app.models import Budget, Connection, FxRate, Schedule, SyncJob, SyncLog, User
from app.services import exports
from app.services.anomalies import anomaly_report
from app.services.datasets import run_dataset_query
from app.services.freshness import freshness_report
from app.services.metrics import query_metrics
from app.services.pacing import pacing_report
from app.services.social_metrics import query_social_metrics
from app.services.web_metrics import query_web_metrics

router = APIRouter(tags=["data"])


def _own_connection(db: Session, user: User, connection_id: int) -> Connection:
    conn = db.get(Connection, connection_id)
    if conn is None or conn.user_id != user.id:
        raise HTTPException(404, "connection not found")
    return conn


# ------------------------------------------------------------------ syncs
class SyncRequest(BaseModel):
    connection_id: int
    date_from: date
    date_to: date
    breakdown: str = "none"


def _job_out(j: SyncJob) -> dict:
    return {
        "id": j.id, "connection_id": j.connection_id, "kind": j.kind,
        "date_from": j.date_from, "date_to": j.date_to, "breakdown": j.breakdown,
        "status": j.status, "stats": j.stats, "error": j.error,
        "created_at": j.created_at, "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


@router.post("/syncs", status_code=201)
def create_sync(
    body: SyncRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_connection(db, user, body.connection_id)
    if body.breakdown not in BREAKDOWN_SETS:
        raise HTTPException(422, f"breakdown must be one of {BREAKDOWN_SETS}")
    if body.date_from > body.date_to:
        raise HTTPException(422, "date_from must be <= date_to")
    job = SyncJob(
        connection_id=body.connection_id, kind="manual",
        date_from=body.date_from, date_to=body.date_to, breakdown=body.breakdown,
    )
    db.add(job)
    db.commit()

    if settings.environment == "test":
        from app.services.sync import run_sync  # inline in tests
        try:
            run_sync(db, job.id)
        except Exception:
            pass  # job row carries status/error either way
        db.refresh(job)
    else:
        from app.workers.tasks import run_sync_task
        run_sync_task.delay(job.id)
    return _job_out(job)


@router.get("/syncs")
def list_syncs(
    connection_id: int | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = Query(50, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(SyncJob)
        .join(Connection, Connection.id == SyncJob.connection_id)
        .where(Connection.user_id == user.id)
        .order_by(SyncJob.id.desc())
    )
    if connection_id:
        stmt = stmt.where(SyncJob.connection_id == connection_id)
    if status:
        stmt = stmt.where(SyncJob.status == status)
    rows = db.scalars(stmt.limit(page_size).offset((page - 1) * page_size))
    return [_job_out(j) for j in rows]


@router.get("/syncs/{sync_id}")
def get_sync(
    sync_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(SyncJob, sync_id)
    if job is None:
        raise HTTPException(404, "sync not found")
    _own_connection(db, user, job.connection_id)
    return _job_out(job)


@router.get("/logs")
def logs(
    sync_job_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(SyncJob, sync_job_id)
    if job is None:
        raise HTTPException(404, "sync not found")
    _own_connection(db, user, job.connection_id)
    rows = db.scalars(
        select(SyncLog).where(SyncLog.sync_job_id == sync_job_id).order_by(SyncLog.id)
    )
    return [
        {"ts": r.ts, "level": r.level, "event": r.event, "data": r.data} for r in rows
    ]


# -------------------------------------------------------------- schedules
class ScheduleRequest(BaseModel):
    connection_id: int
    cadence: str = Field(pattern="^(hourly|every_3h|daily|weekly|manual)$")
    hour_utc: int = Field(3, ge=0, le=23)
    breakdowns: list[str] = ["none"]
    restatement_days: int = Field(7, ge=0, le=90)
    backfill_days: int = Field(365, ge=1, le=1830)
    enabled: bool = True


@router.post("/schedules", status_code=201)
def create_schedule(
    body: ScheduleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _own_connection(db, user, body.connection_id)
    bad = [b for b in body.breakdowns if b not in BREAKDOWN_SETS]
    if bad:
        raise HTTPException(422, f"unknown breakdowns: {bad}")
    s = Schedule(**body.model_dump())
    db.add(s)
    db.commit()
    return {"id": s.id, **body.model_dump()}


@router.get("/schedules")
def list_schedules(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(Schedule)
        .join(Connection, Connection.id == Schedule.connection_id)
        .where(Connection.user_id == user.id)
        .order_by(Schedule.id)
    )
    return [
        {
            "id": s.id, "connection_id": s.connection_id, "cadence": s.cadence,
            "hour_utc": s.hour_utc, "breakdowns": s.breakdowns,
            "restatement_days": s.restatement_days, "backfill_days": s.backfill_days,
            "enabled": s.enabled, "last_enqueued_at": s.last_enqueued_at,
        }
        for s in rows
    ]


@router.patch("/schedules/{schedule_id}")
def toggle_schedule(
    schedule_id: int,
    enabled: bool,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.get(Schedule, schedule_id)
    if s is None:
        raise HTTPException(404, "schedule not found")
    _own_connection(db, user, s.connection_id)
    s.enabled = enabled
    db.commit()
    return {"id": s.id, "enabled": s.enabled}


# ---------------------------------------------------------------- metrics
@router.get("/metrics")
def metrics(
    date_from: date,
    date_to: date,
    breakdown: str = "none",
    platform: list[str] | None = Query(None),
    account_id: list[int] | None = Query(None),
    campaign_id: list[int] | None = Query(None),
    group_by: list[str] | None = Query(None),
    sort: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=1000),
    currency: str | None = Query(
        None, description="Blend spend/value into this reporting currency (ISO 4217)"
    ),
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    if breakdown not in BREAKDOWN_SETS:
        raise HTTPException(422, f"breakdown must be one of {BREAKDOWN_SETS}")
    return query_metrics(
        db,
        date_from=date_from,
        date_to=date_to,
        breakdown=breakdown,
        platforms=platform,
        account_ids=account_id,
        campaign_ids=campaign_id,
        group_by=group_by,
        sort=sort,
        page=page,
        page_size=page_size,
        reporting_currency=currency,
    )


# ----------------------------------------------------------------- budgets
class BudgetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount_monthly: float = Field(gt=0)
    platform: str | None = None       # None = all platforms
    account_id: int | None = None     # None = all accounts in scope
    currency: str = Field("USD", min_length=3, max_length=8)
    enabled: bool = True


def _budget_out(b: Budget) -> dict:
    return {
        "id": b.id, "name": b.name, "platform": b.platform,
        "account_id": b.account_id, "amount_monthly": float(b.amount_monthly),
        "currency": b.currency, "enabled": b.enabled, "created_at": b.created_at,
    }


@router.get("/budgets")
def list_budgets(user: User = Depends(get_read_principal), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Budget).where(Budget.user_id == user.id).order_by(Budget.id)
    )
    return [_budget_out(b) for b in rows]


@router.post("/budgets", status_code=201)
def create_budget(
    body: BudgetIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    b = Budget(
        user_id=user.id, name=body.name, platform=body.platform,
        account_id=body.account_id, amount_monthly=body.amount_monthly,
        currency=body.currency.upper(), enabled=body.enabled,
    )
    db.add(b)
    db.commit()
    return _budget_out(b)


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(
    budget_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    b = db.get(Budget, budget_id)
    if b is None or b.user_id != user.id:
        raise HTTPException(404, "budget not found")
    db.delete(b)
    db.commit()


@router.get("/pacing")
def pacing(user: User = Depends(get_read_principal), db: Session = Depends(get_db)):
    """Month-to-date spend vs budget for each budget, with a projection and an
    over/under/on_track status."""
    return pacing_report(db)


# ---------------------------------------------------------------- anomalies
@router.get("/anomalies")
def anomalies(
    lookback: int = Query(14, ge=3, le=90),
    threshold: float = Query(0.5, gt=0),
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Accounts whose latest day's spend deviates from its trailing average by
    more than `threshold` (spike or drop)."""
    return anomaly_report(db, lookback=lookback, threshold=threshold)


# ---------------------------------------------------------------- freshness
@router.get("/freshness")
def freshness(
    user: User = Depends(get_read_principal), db: Session = Depends(get_db)
):
    """Per-connection data freshness: newest synced day, days behind, and whether
    it has gone stale (stopped delivering)."""
    return freshness_report(db)


# ------------------------------------------------------------------- fx rates
class FxRateIn(BaseModel):
    currency: str = Field(min_length=3, max_length=8)  # ISO 4217, e.g. "SAR"
    rate_to_usd: float = Field(gt=0, description="USD value of 1 unit, e.g. 0.2667")


@router.get("/fx-rates")
def list_fx_rates(
    user: User = Depends(get_read_principal), db: Session = Depends(get_db)
):
    rows = db.scalars(select(FxRate).order_by(FxRate.currency))
    return [
        {"currency": r.currency, "rate_to_usd": float(r.rate_to_usd),
         "updated_at": r.updated_at}
        for r in rows
    ]


@router.put("/fx-rates")
def set_fx_rate(
    body: FxRateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upsert the exchange rate for one currency (owner-only). USD is implicitly
    1.0 and never needs a row."""
    ccy = body.currency.upper()
    row = db.scalar(select(FxRate).where(FxRate.currency == ccy))
    if row is None:
        row = FxRate(currency=ccy, rate_to_usd=body.rate_to_usd)
        db.add(row)
    else:
        row.rate_to_usd = body.rate_to_usd
    db.commit()
    return {"currency": ccy, "rate_to_usd": float(row.rate_to_usd)}


@router.post("/fx-rates/refresh")
def refresh_fx(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Pull the latest rates from the configured public provider (owner-only)."""
    from app.services.fx import refresh_fx_rates
    try:
        n = refresh_fx_rates(db)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to the caller
        raise HTTPException(502, f"fx refresh failed: {exc}") from exc
    return {"updated": n}


@router.get("/web-metrics")
def web_metrics(
    date_from: date,
    date_to: date,
    account_id: list[int] | None = Query(None),
    source: list[str] | None = Query(None),
    group_by: list[str] | None = Query(None),
    sort: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=1000),
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Query GA4 web-analytics facts (sessions/users/conversions/revenue),
    grouped by any of: date, source, medium, campaign, account."""
    return query_web_metrics(
        db,
        date_from=date_from,
        date_to=date_to,
        account_ids=account_id,
        sources=source,
        group_by=group_by,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/social-metrics")
def social_metrics(
    date_from: date,
    date_to: date,
    account_id: list[int] | None = Query(None),
    grain: str | None = None,
    group_by: list[str] | None = Query(None),
    sort: str | None = None,
    page: int = 1,
    page_size: int = Query(100, le=1000),
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Query Facebook Pages / Instagram organic facts (impressions, reach,
    engagement, followers), grouped by date/grain/external_ref/account."""
    return query_social_metrics(
        db,
        date_from=date_from,
        date_to=date_to,
        account_ids=account_id,
        grain=grain,
        group_by=group_by,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@router.get("/accounts")
def accounts(
    platform: str | None = None,
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Accounts discovered across the user's connections (drives dashboard
    filters). One row per ad account / GA4 property / page."""
    from app.models import Account

    stmt = (
        select(Account)
        .join(Connection, Connection.id == Account.connection_id)
        .where(Connection.user_id == user.id)
        .order_by(Account.platform, Account.name)
    )
    if platform:
        stmt = stmt.where(Account.platform == platform)
    return [
        {
            "id": a.id,
            "connection_id": a.connection_id,
            "platform": a.platform,
            "external_id": a.external_id,
            "name": a.name,
            "currency": a.currency,
            "timezone": a.timezone,
        }
        for a in db.scalars(stmt)
    ]


@router.get(
    "/exports",
    tags=["data"],
    summary="Download a metrics query as CSV, JSON, Excel or Parquet",
)
def export(
    dataset: str,
    date_from: date,
    date_to: date,
    format: str = "csv",
    breakdown: str = "none",
    platform: list[str] | None = Query(None),
    account_id: list[int] | None = Query(None),
    campaign_id: list[int] | None = Query(None),
    group_by: list[str] | None = Query(None),
    sort: str | None = None,
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Same filters as the matching query endpoint, but returns a file instead of
    a page. `dataset` is `ads`, `web` or `social`. Works with an API key, so a
    nightly script can pull yesterday's numbers straight into a spreadsheet."""
    if format not in exports.FORMATS:
        raise HTTPException(422, f"format must be one of {exports.FORMATS}")
    rows = run_dataset_query(
        db,
        dataset=dataset,
        date_from=date_from,
        date_to=date_to,
        breakdown=breakdown,
        platforms=platform,
        account_ids=account_id,
        campaign_ids=campaign_id,
        group_by=group_by,
        sort=sort,
        limit=exports.cap(),
    )
    name = exports.filename(dataset, date_from, date_to, format)
    headers = {"content-disposition": f'attachment; filename="{name}"'}

    if format == "csv":
        return StreamingResponse(
            exports.stream_csv(rows),
            media_type=exports.MEDIA_TYPE["csv"],
            headers=headers,
        )
    body = {
        "json": exports.to_json,
        "xlsx": exports.to_xlsx,
        "parquet": exports.to_parquet,
    }[format](rows)
    return Response(
        content=body, media_type=exports.MEDIA_TYPE[format], headers=headers
    )


@router.get("/gtm-resources")
def gtm_resources(
    account_id: int | None = None,
    kind: str | None = None,
    user: User = Depends(get_read_principal),
    db: Session = Depends(get_db),
):
    """Return the latest GTM config snapshot (rows at the most recent
    captured_at), optionally filtered by account and resource kind."""
    from app.models import Account, GtmResource

    latest = db.scalar(
        select(func.max(GtmResource.captured_at))
        .join(Account, Account.id == GtmResource.account_id)
        .where(Account.platform == "gtm")
    )
    if latest is None:
        return {"captured_at": None, "data": []}

    stmt = select(GtmResource).where(GtmResource.captured_at == latest)
    if account_id:
        stmt = stmt.where(GtmResource.account_id == account_id)
    if kind:
        stmt = stmt.where(GtmResource.kind == kind)
    rows = db.scalars(stmt.order_by(GtmResource.id))
    return {
        "captured_at": latest.isoformat(),
        "data": [
            {
                "kind": r.kind, "external_id": r.external_id, "name": r.name,
                "type": r.type, "container_external_id": r.container_external_id,
                "workspace_external_id": r.workspace_external_id, "data": r.data,
            }
            for r in rows
        ],
    }
