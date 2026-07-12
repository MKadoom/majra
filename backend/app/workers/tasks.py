import httpx
import structlog
from celery import Celery, Task

from app.connectors.base import TransientError
from app.core.config import settings
from app.core.db import SessionLocal
from app.services.reports import run_due_reports
from app.services.schedules import tick_schedules
from app.services.sync import run_sync

log = structlog.get_logger()

celery_app = Celery("majra", broker=settings.redis_url)
celery_app.conf.update(
    task_always_eager=(settings.environment == "test"),
    task_eager_propagates=True,
    beat_schedule={
        "tick-schedules": {
            "task": "app.workers.tasks.tick_schedules_task",
            "schedule": 300.0,
        },
        # Saved reports fire on the hour they're set to; checking every 10
        # minutes is enough to catch the window without hammering the DB.
        "run-saved-reports": {
            "task": "app.workers.tasks.run_reports_task",
            "schedule": 600.0,
        },
    },
    timezone="UTC",
)


def _alert(sync_job_id: int, message: str) -> None:
    if not settings.alert_webhook_url:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"event": "sync.failed", "sync_job_id": sync_job_id, "error": message},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", sync_job_id=sync_job_id)


class SyncTask(Task):
    """Fires the alert webhook once, on FINAL failure (after retries)."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        sync_job_id = args[0] if args else kwargs.get("sync_job_id")
        log.error("sync.failed_final", sync_job_id=sync_job_id, error=str(exc))
        _alert(sync_job_id, str(exc))


@celery_app.task(
    base=SyncTask,
    bind=True,
    autoretry_for=(TransientError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def run_sync_task(self, sync_job_id: int) -> None:
    db = SessionLocal()
    try:
        run_sync(db, sync_job_id)
    finally:
        db.close()


@celery_app.task
def tick_schedules_task() -> int:
    db = SessionLocal()
    try:
        job_ids = tick_schedules(db)
        for jid in job_ids:
            run_sync_task.delay(jid)
        return len(job_ids)
    finally:
        db.close()


@celery_app.task
def run_reports_task() -> int:
    db = SessionLocal()
    try:
        return len(run_due_reports(db))
    finally:
        db.close()
