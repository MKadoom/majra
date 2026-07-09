from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.connectors.base import BREAKDOWN_SETS
from app.core.config import settings
from app.core.db import get_db
from app.models import Connection, Schedule, SyncJob, SyncLog, User
from app.services.metrics import query_metrics
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
    user: User = Depends(get_current_user),
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
    )


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
    user: User = Depends(get_current_user),
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
    user: User = Depends(get_current_user),
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


@router.get("/gtm-resources")
def gtm_resources(
    account_id: int | None = None,
    kind: str | None = None,
    user: User = Depends(get_current_user),
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
