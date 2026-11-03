import logging

import structlog
from celery import Celery, Task
from celery.schedules import crontab

from app.connectors.base import TransientError
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Connection
from app.services import alerts
from app.services.anomalies import anomaly_report
from app.services.freshness import stale_connections
from app.services.fx import refresh_fx_rates
from app.services.pacing import over_budget
from app.services.reports import run_due_reports
from app.services.schedules import tick_schedules
from app.services.sync import run_sync

# httpx logs full request URLs at INFO; TikTok/Meta put secrets and tokens in the query.
logging.getLogger("httpx").setLevel(logging.WARNING)

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
        # Token expiry + API sunset check, then the morning digest. Fixed hour
        # (default 05:00 UTC = 08:00 Riyadh) so they land when the operator looks.
        "check-tokens": {
            "task": "app.workers.tasks.check_tokens_task",
            "schedule": crontab(minute=0, hour=alerts.alert_settings.alert_digest_hour_utc),
        },
        "daily-digest": {
            "task": "app.workers.tasks.daily_digest_task",
            "schedule": crontab(minute=5, hour=alerts.alert_settings.alert_digest_hour_utc),
        },
        # Refresh exchange rates weekly from the public provider.
        "refresh-fx": {
            "task": "app.workers.tasks.refresh_fx_task",
            "schedule": 604800.0,
        },
    },
    timezone="UTC",
)


def _send(alert) -> None:
    """Deliver an alert; never let a channel problem break a task."""
    if alert is None:
        return
    try:
        alerts.send(alert)
    except Exception as exc:  # noqa: BLE001
        log.warning("alert.send_failed", kind=alert.kind, error=type(exc).__name__)


class SyncTask(Task):
    """Alerts once, on FINAL failure (after retries)."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        sync_job_id = args[0] if args else kwargs.get("sync_job_id")
        log.error("sync.failed_final", sync_job_id=sync_job_id, error=str(exc))
        db = SessionLocal()
        try:
            _send(alerts.sync_failed(db, sync_job_id, str(exc)))
        finally:
            db.close()


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
        job = run_sync(db, sync_job_id)
        # A "successful" sync can still have skipped accounts or carry an API
        # deprecation warning — both need a human, so alert on them.
        _send(alerts.accounts_skipped(db, job))
        warnings = (job.stats or {}).get("api_warnings") or []
        if warnings:
            platform = db.get(Connection, job.connection_id).platform
            _send(alerts.api_version_alert(
                [{"platform": platform, "version": "current", "warning": w}
                 for w in warnings]
            ))
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


@celery_app.task
def check_freshness_task() -> int:
    db = SessionLocal()
    try:
        stale = stale_connections(db)
        if stale:
            log.warning("data.stale", count=len(stale),
                        connections=[s["connection_id"] for s in stale])
            _send(alerts.Alert(
                kind="data.stale", title="data stopped arriving",
                lines=[f"{s['platform']} · {s['name']}: last day {s['last_date']} "
                       f"({s['days_stale']} day(s) behind)" for s in stale],
                payload={"connections": stale},
            ))
        return len(stale)
    finally:
        db.close()


@celery_app.task
def check_budgets_task() -> int:
    db = SessionLocal()
    try:
        over = over_budget(db)
        if over:
            log.warning("budget.over", count=len(over),
                        budgets=[b["budget_id"] for b in over])
            _send(alerts.Alert(
                kind="budget.over", title="budget projected to overspend",
                lines=[f"{b['name']}: {b['spent_mtd']:,.2f} spent, projected "
                       f"{b['projected_month_end']:,.2f} of {b['amount_monthly']:,.2f} "
                       f"{b['currency']}" for b in over],
                payload={"budgets": over},
            ))
        return len(over)
    finally:
        db.close()


@celery_app.task
def check_anomalies_task() -> int:
    db = SessionLocal()
    try:
        found = anomaly_report(db)
        if found:
            log.warning("spend.anomaly", count=len(found),
                        accounts=[a["account"] for a in found])
            _send(alerts.Alert(
                kind="spend.anomaly", title="spend anomaly",
                lines=[f"{a['account']} {a['date']}: {a['direction']} "
                       f"{a['latest_spend']:,.2f} vs avg {a['avg_spend']:,.2f}"
                       for a in found],
                payload={"anomalies": found},
            ))
        return len(found)
    finally:
        db.close()


@celery_app.task
def check_tokens_task() -> int:
    """Daily: alert on tokens expiring within 14 days and on pinned API
    versions near their published sunset."""
    db = SessionLocal()
    try:
        expiring = alerts.expiring_tokens(db)
        _send(alerts.token_expiring(expiring))
        versions = alerts.api_version_notices()
        _send(alerts.api_version_alert(versions))
        return len(expiring) + len(versions)
    finally:
        db.close()


@celery_app.task
def daily_digest_task() -> bool:
    db = SessionLocal()
    try:
        digest = alerts.daily_digest(db)
        _send(digest)
        return True
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
