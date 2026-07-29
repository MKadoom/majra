import httpx
import structlog
from celery import Celery, Task

from app.connectors.base import TransientError
from app.core.config import settings
from app.core.db import SessionLocal
from app.services.anomalies import anomaly_report
from app.services.freshness import stale_connections
from app.services.fx import refresh_fx_rates
from app.services.pacing import over_budget
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
        # Freshness sweep once a day: alert on any connection whose data has
        # gone stale (source stopped, or sync failing).
        "check-freshness": {
            "task": "app.workers.tasks.check_freshness_task",
            "schedule": 86400.0,
        },
        # Budget pacing sweep once a day: alert on budgets projected to overspend.
        "check-budgets": {
            "task": "app.workers.tasks.check_budgets_task",
            "schedule": 86400.0,
        },
        # Spend anomaly sweep once a day: alert on overnight spikes/drops.
        "check-anomalies": {
            "task": "app.workers.tasks.check_anomalies_task",
            "schedule": 86400.0,
        },
        # Refresh exchange rates weekly from the public provider.
        "refresh-fx": {
            "task": "app.workers.tasks.refresh_fx_task",
            "schedule": 604800.0,
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


def _freshness_alert(stale: list[dict]) -> None:
    if not settings.alert_webhook_url or not stale:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"event": "data.stale", "connections": stale},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", event="data.stale")


@celery_app.task
def check_freshness_task() -> int:
    db = SessionLocal()
    try:
        stale = stale_connections(db)
        if stale:
            log.warning("data.stale", count=len(stale),
                        connections=[s["connection_id"] for s in stale])
            _freshness_alert(stale)
        return len(stale)
    finally:
        db.close()


def _budget_alert(over: list[dict]) -> None:
    if not settings.alert_webhook_url or not over:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"event": "budget.over", "budgets": over},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", event="budget.over")


@celery_app.task
def check_budgets_task() -> int:
    db = SessionLocal()
    try:
        over = over_budget(db)
        if over:
            log.warning("budget.over", count=len(over),
                        budgets=[b["budget_id"] for b in over])
            _budget_alert(over)
        return len(over)
    finally:
        db.close()


def _anomaly_alert(anomalies: list[dict]) -> None:
    if not settings.alert_webhook_url or not anomalies:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"event": "spend.anomaly", "anomalies": anomalies},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("alert.webhook_failed", event="spend.anomaly")


@celery_app.task
def check_anomalies_task() -> int:
    db = SessionLocal()
    try:
        found = anomaly_report(db)
        if found:
            log.warning("spend.anomaly", count=len(found),
                        accounts=[a["account"] for a in found])
            _anomaly_alert(found)
        return len(found)
    finally:
        db.close()


@celery_app.task
def refresh_fx_task() -> int:
    db = SessionLocal()
    try:
        n = refresh_fx_rates(db)
        log.info("fx.refreshed", currencies=n)
        return n
    except Exception as exc:  # noqa: BLE001 — provider hiccup shouldn't crash beat
        log.warning("fx.refresh_failed", error=str(exc))
        return 0
    finally:
        db.close()
