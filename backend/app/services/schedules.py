"""Schedule ticking (Phase 3). Beat calls tick_schedules every 5 minutes;
each due schedule turns into SyncJob rows (one per breakdown set), or into
month-chunked backfill jobs on first run (SPEC §4)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Schedule, SyncJob
from app.services.sync import backfill_chunks, incremental_range

CADENCE_DELTA = {
    "hourly": timedelta(hours=1),
    "every_3h": timedelta(hours=3),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _is_due(s: Schedule, now: datetime) -> bool:
    if s.cadence == "manual" or not s.enabled:
        return False
    delta = CADENCE_DELTA.get(s.cadence)
    if delta is None:
        return False
    if s.cadence in ("daily", "weekly") and now.hour != s.hour_utc:
        return False
    if s.last_enqueued_at is None:
        return True
    return (now - s.last_enqueued_at) >= (delta - timedelta(minutes=10))


def tick_schedules(db: Session, now: datetime | None = None) -> list[int]:
    """Returns the SyncJob ids created this tick (caller enqueues them)."""
    now = now or datetime.now(timezone.utc)
    created: list[int] = []
    for s in db.scalars(select(Schedule).where(Schedule.enabled.is_(True))):
        if not _is_due(s, now):
            continue
        breakdowns = s.breakdowns or ["none"]
        for bd in breakdowns:
            rng = incremental_range(
                db, s.connection_id, bd, s.restatement_days, today=now.date()
            )
            if rng is None:  # first run → backfill
                for start, end in backfill_chunks(s.backfill_days, today=now.date()):
                    job = SyncJob(
                        connection_id=s.connection_id, kind="backfill",
                        date_from=start, date_to=end, breakdown=bd,
                    )
                    db.add(job)
                    db.flush()
                    created.append(job.id)
            else:
                start, end = rng
                if start > end:
                    continue
                job = SyncJob(
                    connection_id=s.connection_id, kind="scheduled",
                    date_from=start, date_to=end, breakdown=bd,
                )
                db.add(job)
                db.flush()
                created.append(job.id)
        s.last_enqueued_at = now
    db.commit()
    return created
